"""
qtui/seed_library.py — (19/11) the Seed Library: the seeds worth keeping, each with its own
board.

    SeedLibraryWindow   the window (opened by the ★ of the top bar and from Home)
      Overview          shelves (All, Favourites, the user's libraries), search, sort and the
                        seed cards (a cover drawn from the seed's own map, its name, tags)
      SeedPage          a seed: its header (sigil, number, name, tags, favourite, libraries,
                        « Use this seed ») and its board — blocks placed freely, moved by their
                        grip, resized by their corner: screenshots (file, paste, drop),
                        mini-maps (live: drag / wheel; or frozen), notes, coordinates
    renderer()          the background maker of the biome pictures (covers, mini-maps)

The data lives in qtui/seed_store.py.
"""
from __future__ import annotations

import math
import os
import queue
import threading
import time

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal, QEasingCurve
from PySide6.QtGui import (QColor, QDrag, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
                           QIcon, QPen, QPixmap)
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget)

from . import brand, icons, motion, theme
from .seed_store import BLOCK_SIZES, image_path, store, thumb_path
from .widgets import FlowLayout, user_path

GOLD = "#f5b942"                      # the library's own colour (its star)
GRID = 8                              # the board's snapping step
MIME = "application/x-ssa-seed"
MAP_STRUCTS = ("village", "pillager_outpost", "mansion", "monument", "desert_pyramid", "jungle_temple",
               "swamp_hut", "igloo", "ancient_city", "ruined_portal", "stronghold")
# (19/11 ter) what a mini-map can be coloured by: the biomes, or a noise map (Exact Y: too slow here)
MAP_NOISES = ("Temperature", "Humidity", "Continentalness", "Erosion", "Weirdness", "Approx height")
CLIMATE_SINCE = 22                   # MC_1_18: the climate noises exist from 1.18
# (19/11 bis) what a mini-map can show (the editor's chips), in this order
MAP_CHOICES = ("village", "pillager_outpost", "mansion", "monument", "stronghold", "desert_pyramid",
               "jungle_temple", "swamp_hut", "igloo", "ancient_city", "trial_chambers", "trail_ruins",
               "ruined_portal", "shipwreck", "ocean_ruin", "buried_treasure", "abandoned_camp", "desert_well")


def struct_ok(key, mc):
    """The structure exists in this version."""
    import cubiomes_wrap as cw
    if key == "stronghold":
        return True
    if key not in cw.ALL_STRUCTURE_TYPES or cw.STRUCTURE_DIM.get(key, 0) != 0:
        return False
    return mc >= cw.STRUCTURE_MIN_VERSION.get(key, 0)


def _mc(version):
    import cubiomes_wrap as cw
    return cw.MC_VERSIONS.get(version) or cw.MC_VERSIONS[list(cw.MC_VERSIONS)[-1]]


def world_key(s):
    return (int(s["seed"]), _mc(s.get("version")), bool(s.get("large")))


def label(s):
    return s.get("name") or f"Seed {s['seed']}"


def version_short(s):
    try:
        from .world import current_exact
        return current_exact(s.get("version")) or s.get("version", "").replace("Java ", "")
    except Exception:
        return (s.get("version") or "").replace("Java ", "")


# ── pictures of the worlds, made in the background ───────────────────────────
class _Renderer(QObject):
    """One worker thread; each requester (an owner key) keeps only its last request."""
    done = Signal(object, object, object, object)        # owner, token, QImage, info
    cover_saved = Signal(str)                            # (20/11) a seed's cover changed (its id)

    def __init__(self):
        super().__init__()
        self.palette = None
        self._q = queue.PriorityQueue()             # (19/11 bis) the editor's pictures first
        self._n = 0
        self._pending = {}
        self._lock = threading.Lock()
        self._spawn = {}
        self._sh = {}                                    # strongholds of each world
        threading.Thread(target=self._loop, daemon=True, name="ssa-seedlib").start()

    def _put(self, owner, prio):
        self._n += 1
        self._q.put((prio, self._n, owner))

    def request(self, owner, token, wkey, cx, cz, scale, w, h, structs=True, around_spawn=False, prio=1,
                noise=None):
        """noise: (name, palette, opacity) — the picture coloured by that noise map."""
        with self._lock:
            new = owner not in self._pending
            self._pending[owner] = (token, wkey, cx, cz, scale, w, h, structs, around_spawn, self.palette, noise)
        if new:
            self._put(owner, prio)

    def call(self, owner, token, fn, prio=0):
        """fn() run on the worker; its result comes back as info["call"]."""
        with self._lock:
            new = owner not in self._pending
            self._pending[owner] = (token, "__call__", fn)
        if new:
            self._put(owner, prio)

    def _find_spawn(self, wkey):
        """The world's spawn (the map's: 1.18+ the game's own, ≈ 1 s), kept."""
        sp = self._spawn.get(wkey)
        if sp is None:
            from .map_engine import _thread_lib
            try:
                p = _thread_lib(wkey, 0).get_spawn()
                sp = (int(p[0]), int(p[1]))
            except Exception:                            # noqa: BLE001
                sp = (0, 0)
            self._spawn[wkey] = sp
        return sp

    def spawn_of(self, wkey):
        return self._spawn.get(wkey)

    def strongholds(self, lib, wkey):
        got = self._sh.get(wkey)
        if got is None:
            try:
                got = [(int(a), int(b)) for a, b in lib.find_strongholds(max_count=128)]
            except Exception:                            # noqa: BLE001
                got = []
            self._sh[wkey] = got
        return got

    def _loop(self):
        while True:
            owner = self._q.get()[2]
            with self._lock:
                job = self._pending.pop(owner, None)
            if job is None:
                continue
            try:
                if job[1] == "__call__":
                    img, info = None, dict(call=job[2]())
                else:
                    img, info = self._make(*job[1:])
            except Exception as e:                       # noqa: BLE001
                img, info = None, dict(error=str(e))
            try:
                self.done.emit(owner, job[0], img, info)
            except RuntimeError:
                return

    def _make(self, wkey, cx, cz, scale, w, h, structs, around_spawn, palette, noise=None):
        import numpy as np
        import cubiomes_wrap as cw
        from .map_engine import _thread_lib, biome_lut, _pixmap_from_rgb
        lib = _thread_lib(wkey, 0)
        sp = self._spawn.get(wkey)
        if around_spawn:
            cx, cz = sp = self._find_spawn(wkey)        # (19/11) the map's spawn (1.18+: the game's own)
        elif sp is None:                                 # (19/11 bis) the picture now, the spawn after
            self.call(("spawn", wkey), 0, lambda: self._find_spawn(wkey), prio=2)
        x0, z0 = int(cx) // scale - w // 2, int(cz) // scale - h // 2
        ids = np.frombuffer(lib.gen_biomes_region(x0, z0, w, h, scale), dtype=np.int32).reshape(h, w)
        lut = biome_lut(palette) if palette else biome_lut("True Colors (ShowMe7)")
        rgb = lut[np.clip(ids, 0, len(lut) - 1)]
        nerr = None
        if noise and noise[0]:                           # (19/11 ter) a noise map, over the biomes
            try:
                nrgb = noise_rgb(wkey, noise[0], noise[1], x0, z0, w, h, scale,
                                 noise[3] if len(noise) > 3 else None)
                a = max(0.0, min(1.0, float(noise[2] if len(noise) > 2 else 1.0)))
                rgb = nrgb if a >= 0.999 else np.clip(rgb * (1.0 - a) + nrgb * a + 0.5, 0, 255).astype(np.uint8)
            except Exception as e:                       # noqa: BLE001
                nerr = str(e)
        img = _pixmap_from_rgb(rgb)
        found = []
        keys = MAP_STRUCTS if structs is True else tuple(structs or ())
        if keys:
            for key in keys:
                if not struct_ok(key, wkey[1]):
                    continue
                if key == "stronghold":
                    bx0, bz0, bx1, bz1 = x0 * scale, z0 * scale, (x0 + w) * scale, (z0 + h) * scale
                    found += [("stronghold", a, b) for a, b in self.strongholds(lib, wkey)
                              if bx0 <= a <= bx1 and bz0 <= b <= bz1]
                    continue
                t = cw.ALL_STRUCTURE_TYPES.get(key)
                if t is None:
                    continue
                try:
                    pts = lib.find_structures_in_box(t, x0 * scale, z0 * scale, (x0 + w) * scale,
                                                     (z0 + h) * scale, max_count=24)
                except Exception:
                    continue
                found += [(key, int(a), int(b)) for a, b in pts]
        return img, dict(x0=x0 * scale, z0=z0 * scale, scale=scale, structs=found, spawn=sp, cx=cx, cz=cz,
                         noise_error=nerr)


def noise_ok(noise, mc):
    return noise == "Approx height" or (noise in MAP_NOISES and mc >= CLIMATE_SINCE)


def noise_rgb(wkey, noise, palette, x0, z0, w, h, scale, octave=None):
    """(19/11 ter) The noise map of the region (cells x0, z0, w × h at 1:scale) coloured by a
    noise palette — as the map's noise layer computes and colours it (map_engine)."""
    import numpy as np
    import noise_palettes as npal
    from .map_engine import (CLIMATE, HEIGHT_RANGE, _thread_height, _thread_noise, noise_lut, noise_range,
                             octave_arrays)
    if noise in CLIMATE:
        oc = tuple(octave) if octave else None
        arr = np.asarray(octave_arrays(_thread_noise(wkey, noise), x0, z0, w, h, scale, oc), np.float32)
        if oc is not None and oc[2]:                  # one octave alone: its own range (as the map)
            m = max(1e-6, float(np.abs(arr).max()) * 1.1)
            vmin, vmax = -m, m
        else:
            vmin, vmax = noise_range(noise)
    elif noise == "Approx height":
        hm = _thread_height(wkey)
        OVL = 8
        stride = max(1, scale // 4)
        nx = max(2, -(-(w * scale) // (stride * 4)) + 1)
        nz = max(2, -(-(h * scale) // (stride * 4)) + 1)
        full, _ = hm.gen_height_grid(x0 * scale // 4 - OVL * stride, z0 * scale // 4 - OVL * stride,
                                     nx + 2 * OVL, nz + 2 * OVL, step=stride)
        samp = np.asarray(full, np.float32)[OVL:OVL + nz, OVL:OVL + nx]
        # cell j sits at sample j·scale / (4·stride): bilinear between the samples
        fx = np.arange(w, dtype=np.float32) * scale / (4.0 * stride)
        fz = np.arange(h, dtype=np.float32) * scale / (4.0 * stride)
        ix = np.minimum(fx.astype(np.int32), nx - 2)
        iz = np.minimum(fz.astype(np.int32), nz - 2)
        tx = (fx - ix)[None, :]
        tz = (fz - iz)[:, None]
        rows = samp[iz] * (1 - tz) + samp[iz + 1] * tz
        samp = rows[:, ix] * (1 - tx) + rows[:, ix + 1] * tx
        arr = samp
        vmin, vmax = HEIGHT_RANGE
    else:
        raise ValueError(f"no such noise map here: {noise}")
    pos = (arr - vmin) / max(vmax - vmin, 1e-6)
    u = npal.u_from_pos(pos, npal.editor_noise(noise))
    raw = np.clip(np.rint(np.asarray(u) * 255.0), 0, 255).astype(np.uint8)
    if noise == "Approx height":                          # the sinkhole level: its own colours
        sk = npal.sink_index()
        raw = np.where(arr <= npal.SINK_Y, np.minimum(raw, sk), np.maximum(raw, sk + 1)).astype(np.uint8)
    pal = palette if palette in npal.NOISE_PALETTE_NAMES or palette in npal.CUSTOM_PALETTES else "Semantic"
    return noise_lut(noise, pal)[raw]


def default_noise_palette(win=None):
    """The main map's noise palette (else the default one)."""
    try:
        return win.map.ov["palette"]
    except Exception:                                    # noqa: BLE001
        import noise_palettes as npal
        return getattr(npal, "_DEFAULT_NOISE_PALETTE", "Semantic")


_R = []


def renderer() -> _Renderer:
    if not _R:
        _R.append(_Renderer())
    return _R[0]


def _rounded(img, w, h, radius, mode="cover", top_only=False):
    """A picture laid into a w × h rounded rectangle (cover: filled, cropped; contain: whole)."""
    dpr = 2.0
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    if top_only:
        path.addRoundedRect(QRectF(0, 0, w, h + radius), radius, radius)
    else:
        path.addRoundedRect(QRectF(0, 0, w, h), radius, radius)
    p.setClipPath(path)
    iw, ih = img.width(), img.height()
    k = max(w / iw, h / ih) if mode == "cover" else min(w / iw, h / ih)
    dw, dh = iw * k, ih * k
    p.drawImage(QRectF((w - dw) / 2, (h - dh) / 2, dw, dh), img)
    p.end()
    return pm


def _fade(w, ms=220):
    try:
        motion.fade_in(w, ms)
    except RuntimeError:                     # gone meanwhile (the list was made again)
        pass


def _rise(w, dx=0, dy=14, ms=420, delay=0):
    """(19/11 bis) w comes in: from (dx, dy) away to its place, fading in. Its place is the one
    its layout gives it when the move starts; the layout takes it back at the end."""
    if not motion.enabled():
        return
    try:
        fx = QGraphicsOpacityEffect(w)
        fx.setOpacity(0.0)
        w.setGraphicsEffect(fx)
    except RuntimeError:
        return

    def go():
        try:
            home = w.pos()
        except RuntimeError:
            return

        def step(t):
            try:
                fx.setOpacity(t)
                w.move(home.x() + int(round(dx * (1 - t))), home.y() + int(round(dy * (1 - t))))
            except RuntimeError:
                pass

        def end():
            try:
                w.setGraphicsEffect(None)
                w.move(home)
                pw = w.parentWidget()
                if pw is not None and pw.layout() is not None:
                    pw.layout().invalidate()
            except RuntimeError:
                pass
        motion.run(w, ms, step, end)
    QTimer.singleShot(int(delay), go)


def _cover_image(s):
    """The picture that stands for a seed: its first screenshot, else its saved cover map."""
    for b in s.get("board", []):
        if b["kind"] == "shot" and os.path.isfile(image_path(b["id"])):
            img = QImage(image_path(b["id"]))
            if not img.isNull():
                return img
    p = thumb_path(s["id"])
    if os.path.isfile(p):
        img = QImage(p)
        if not img.isNull():
            return img
    return None


# ── the overview: shelves, cards ─────────────────────────────────────────────
class SeedCard(QWidget):
    """A seed on its shelf: the cover (its world from above, or its first screenshot), its name,
    number, version and tags; a star for the favourites. Lifts a little under the mouse."""
    opened = Signal(str)
    W, H, CH = 244, 214, 128

    def __init__(self, s, parent=None):
        super().__init__(parent)
        self.s = s
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self._hov, self._an = 0.0, None
        self._cover = None
        self._press = None
        self._load_cover()

    def _load_cover(self):
        img = _cover_image(self.s)
        if img is not None:
            self._cover = _rounded(img, self.W - 12, self.CH, 14, top_only=True)
        mb = cover_map(self.s)
        if mb is not None and self.s.get("cover") != cover_key(mb):
            # (20/11) the board's first mini-map, as it shows there (not the spawn at 1:16)
            d = mb["data"]
            sc, w, h = view_cells(map_bpp(d), mb["w"], mb["h"])
            renderer().request(("card", self.s["id"]), ("map", mb["id"]), world_key(self.s), d.get("x") or 0,
                               d.get("z") or 0, sc, w, h, structs=map_structs(d), around_spawn=d.get("x") is None,
                               noise=map_noise(d, getattr(self.window(), "win", None)))
        elif img is None:                       # made in the background, kept as a thumbnail
            renderer().request(("card", self.s["id"]), self.s["id"], world_key(self.s), 0, 0, 16,
                               180, 96, structs=False, around_spawn=True)

    def cover_in(self, img):
        if img is None:                         # no picture (a version gone…): the sigil's colours
            img = QImage(64, 40, QImage.Format.Format_RGB32)
            p = QPainter(img)
            g = QLinearGradient(0, 0, 64, 40)
            g.setColorAt(0, QColor(theme.BG3))
            g.setColorAt(1, QColor(GOLD).darker(220))
            p.fillRect(img.rect(), g)
            p.end()
        else:
            try:
                img.save(thumb_path(self.s["id"]))
            except Exception:
                pass
        self._cover = _rounded(img, self.W - 12, self.CH, 14, top_only=True)
        self.update()

    def _star_rect(self):
        return QRectF(self.W - 6 - 38, 12, 30, 30)

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
        self._an = motion.run(self, 180, step)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.position()

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.position() - self._press).manhattanLength() > 10:
            self._press = None                  # a drag: onto a library of the sidebar
            d = QDrag(self)
            md = QMimeData()
            md.setData(MIME, self.s["id"].encode())
            d.setMimeData(md)
            pm = self.grab().scaled(self.W // 2, self.H // 2, Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
            d.setPixmap(pm)
            d.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
            d.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        self._press = None
        if self._star_rect().contains(e.position()):
            self.s["fav"] = not self.s.get("fav")
            store().touch(seed=self.s)
            self.update()
            return
        self.opened.emit(self.s["id"])

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self._hov
        lift = 3 * k
        r = QRectF(6, 6 - lift, self.W - 12, self.H - 12)
        if k > 0.01:                             # a glow under the lifted card
            g = QColor(GOLD)
            g.setAlphaF(0.16 * k)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(r.adjusted(-4, 2, 4, 8), 18, 18)
        bg = QColor(theme.BG2)
        bg.setAlphaF(0.92)
        edge = motion.mix(theme.LINE, GOLD, 0.6 * k)
        p.setPen(QPen(edge, 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 14, 14)
        cr = QRectF(r.left(), r.top(), r.width(), self.CH)
        if self._cover is not None:
            p.drawPixmap(cr, self._cover, QRectF(self._cover.rect()))
        else:                                    # while it is made: a shimmer
            path = QPainterPath()
            path.addRoundedRect(QRectF(cr.left(), cr.top(), cr.width(), cr.height() + 14), 14, 14)
            p.save()
            p.setClipRect(cr)
            p.fillPath(path, QColor(theme.BG3))
            t = (time.monotonic() * 0.8) % 1.0
            gr = QLinearGradient(cr.left() + cr.width() * (t * 2 - 0.5), 0, cr.left() + cr.width() * (t * 2), 0)
            c0 = QColor(255, 255, 255, 0)
            c1 = QColor(255, 255, 255, 22)
            gr.setColorAt(0, c0)
            gr.setColorAt(0.5, c1)
            gr.setColorAt(1, c0)
            p.fillPath(path, gr)
            p.restore()
            QTimer.singleShot(40, self.update)
        fade = QLinearGradient(0, cr.bottom() - 40, 0, cr.bottom())   # the cover melts into the card
        c0 = QColor(bg); c0.setAlphaF(0.0)
        fade.setColorAt(0, c0)
        fade.setColorAt(1, bg)
        p.fillRect(QRectF(cr.left(), cr.bottom() - 40, cr.width(), 40), fade)
        sg = brand.sigil(int(self.s["seed"]), 34)                    # the sigil, half on the cover
        p.drawPixmap(QRectF(r.left() + 12, cr.bottom() - 22, 34, 34), sg, QRectF(sg.rect()))
        sr = self._star_rect().translated(0, -lift)                   # the favourite star
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(10, 14, 22, 150))
        p.drawEllipse(sr)
        on = bool(self.s.get("fav"))
        st = icons.pixmap("star_fill" if on else "star", 18, GOLD if on else "#ffffff")
        p.drawPixmap(QRectF(sr.center().x() - 9, sr.center().y() - 9, 18, 18), st, QRectF(st.rect()))
        x0 = r.left() + 54
        p.setPen(QColor(theme.TEXT))
        f = theme.ui_font(10.2, QFont.Weight.DemiBold)
        p.setFont(f)
        p.drawText(QRectF(x0, cr.bottom() + 2, r.right() - x0 - 10, 20), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(label(self.s), Qt.TextElideMode.ElideRight, int(r.right() - x0 - 10)))
        p.setFont(theme.mono_font(8.2))
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(r.left() + 12, cr.bottom() + 24, r.width() - 24, 16), Qt.AlignmentFlag.AlignVCenter,
                   self.s["seed"] if self.s.get("name") else "")
        chips = [version_short(self.s)] + (["Large"] if self.s.get("large") else []) + list(self.s.get("tags") or [])
        p.setFont(theme.ui_font(7.8, QFont.Weight.DemiBold))
        fm = p.fontMetrics()
        x, y = r.left() + 12, r.bottom() - 26
        for i, t in enumerate(chips):
            tw = fm.horizontalAdvance(t) + 14
            if x + tw > r.right() - 10:
                break
            col = QColor(GOLD) if i == 0 else QColor(theme.TEXT2)
            bgc = QColor(col)
            bgc.setAlphaF(0.14)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bgc)
            p.drawRoundedRect(QRectF(x, y, tw, 18), 9, 9)
            p.setPen(col)
            p.drawText(QRectF(x, y, tw, 18), Qt.AlignmentFlag.AlignCenter, t)
            x += tw + 5


class _ShelfButton(QPushButton):
    """A library in the sidebar: its colour, its name, how many seeds; seeds can be dropped on it."""
    dropped = Signal(str, str)                     # shelf id, seed id

    def __init__(self, sid, text, color, count):
        super().__init__()
        self.sid, self.color, self.count = sid, color, count
        self.setText(text)
        self.setObjectName("ShelfButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(36)
        self.setAcceptDrops(sid not in ("__all", "__fav"))
        self._flash = 0.0

    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()
            self._flash = 0.6
            self.update()

    def dragLeaveEvent(self, e):
        self._flash = 0.0
        self.update()

    def dropEvent(self, e):
        sid = bytes(e.mimeData().data(MIME)).decode()
        self.dropped.emit(self.sid, sid)

        def step(t):
            self._flash = 1.0 - t
            self.update()
        motion.run(self, 600, step) if motion.enabled() else step(1.0)

    def paintEvent(self, e):
        super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        c = QColor(self.color)
        if self.isChecked() or self._flash > 0:
            g = QColor(c)
            g.setAlphaF(0.16 + 0.25 * self._flash)
            p.setPen(QPen(QColor(c.red(), c.green(), c.blue(), 110), 1))
            p.setBrush(g)
            p.drawRoundedRect(r, 10, 10)
        if self.sid == "__fav":
            st = icons.pixmap("star_fill", 15, GOLD)
            p.drawPixmap(QRectF(12, r.center().y() - 7.5, 15, 15), st, QRectF(st.rect()))
        elif self.sid == "__all":
            st = icons.pixmap("seed_library", 15, theme.TEXT2)
            p.drawPixmap(QRectF(12, r.center().y() - 7.5, 15, 15), st, QRectF(st.rect()))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(19.5, r.center().y()), 5, 5)
        p.setPen(QColor(theme.TEXT if self.isChecked() else theme.TEXT2))
        p.setFont(theme.ui_font(9.4, QFont.Weight.DemiBold if self.isChecked() else QFont.Weight.Normal))
        p.drawText(QRectF(36, r.top(), r.width() - 80, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(r.width() - 80)))
        p.setFont(theme.num_font(8.4))
        p.setPen(QColor(theme.TEXT3))
        p.drawText(QRectF(r.right() - 44, r.top(), 34, r.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(self.count))


class Overview(QWidget):
    """The shelves on the left, the seed cards on the right."""
    open_seed = Signal(str)
    add_current = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shelf = "__all"
        self.cards = {}
        self.rise_next = False                       # (the next list comes in rising: the opening)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(14)
        side = self.side = QFrame()
        side.setObjectName("SidePanel")
        side.setFixedWidth(232)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(12, 16, 12, 12)
        sv.setSpacing(4)
        top = QHBoxLayout()
        mk = QLabel()
        mk.setFixedSize(26, 26)
        mk.setPixmap(icons.pixmap("star_fill", 24, GOLD))
        top.addWidget(mk)
        t = QLabel("Seed Library")
        t.setObjectName("PageTitle")
        top.addWidget(t)
        top.addStretch(1)
        sv.addLayout(top)
        sv.addSpacing(10)
        self.shelf_box = QVBoxLayout()
        self.shelf_box.setSpacing(2)
        sv.addLayout(self.shelf_box)
        sv.addSpacing(6)
        nb = QPushButton("  New library")
        nb.setObjectName("CardLink")
        icons.bind(nb, "plus", 15)
        nb.setCursor(Qt.CursorShape.PointingHandCursor)
        nb.clicked.connect(self._new_shelf)
        sv.addWidget(nb, 0, Qt.AlignmentFlag.AlignLeft)
        sv.addStretch(1)
        hint = QLabel("Drag a card onto a library to sort it.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 8pt;")
        sv.addWidget(hint)
        h.addWidget(side)

        main = QVBoxLayout()
        main.setSpacing(10)
        self.bar_w = QWidget()
        bar = QHBoxLayout(self.bar_w)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        self.title = QLabel("All seeds")
        self.title.setObjectName("SetPageTitle")
        bar.addWidget(self.title)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        bar.addWidget(self.count)
        bar.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search a name, a seed, a tag, a note…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(280)
        self.search.addAction(icons.icon("search", 16, theme.TEXT3), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(lambda *_: self.refresh())
        bar.addWidget(self.search)
        from PySide6.QtWidgets import QComboBox
        self.sort = QComboBox()
        self.sort.addItems(["Recent first", "Favourites first", "Name"])
        self.sort.currentIndexChanged.connect(lambda *_: self.refresh())
        bar.addWidget(self.sort)
        new = QPushButton("  Add a seed")
        new.setProperty("kind", "ghost")
        icons.bind(new, "plus", 16)
        new.setToolTip("Type a seed in (or paste one)  ·  Ctrl+N")
        new.clicked.connect(self.add_seed)
        bar.addWidget(new)
        add = QPushButton("  Keep the current seed")
        add.setProperty("kind", "primary")
        icons.bind(add, "star_fill", 16, "on_accent", "on_accent")
        add.clicked.connect(self.add_current.emit)
        self._add_shine = motion.Shine(add)
        bar.addWidget(add)
        main.addWidget(self.bar_w)
        self.grid_host = QWidget()
        self.flow = FlowLayout(self.grid_host, 12, 12)
        sa = self.sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(self.grid_host)
        sa.verticalScrollBar().setSingleStep(24)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("Muted")
        self.empty.setWordWrap(True)
        main.addWidget(sa, 1)
        main.addWidget(self.empty, 1)
        h.addLayout(main, 1)
        renderer().done.connect(self._rendered)
        renderer().cover_saved.connect(self._cover_saved)
        store().changed.connect(self._store_changed)
        self.refresh()

    def _cover_saved(self, sid):
        c = self.cards.get(sid)
        if c is not None:
            try:
                c._load_cover()
                c.update()
            except RuntimeError:
                pass

    def _rendered(self, owner, token, img, info):
        if isinstance(owner, tuple) and owner[0] == "card":
            c = self.cards.get(owner[1])
            if isinstance(token, tuple) and token[0] == "map":        # a mini-map cover
                s = store().get(owner[1])
                mb = next((b for b in (s or {}).get("board", []) if b["id"] == token[1]), None)
                if mb is not None and isinstance(img, QImage) and not img.isNull():
                    save_map_cover(s, mb, img, info)                  # (→ cover_saved → the card)
                return
            if c is not None:
                try:
                    c.cover_in(img if isinstance(img, QImage) and not img.isNull() else None)
                except RuntimeError:
                    pass

    def _store_changed(self, what):
        if what in ("seeds", "shelves"):
            self.refresh()
        elif what in self.cards:
            self._fill_shelves()
            self.cards[what].update()

    def _fill_shelves(self):
        while self.shelf_box.count():
            it = self.shelf_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        st = store()
        rows = [("__all", "All seeds", theme.TEXT2, len(st.seeds)),
                ("__fav", "Favourites", GOLD, sum(1 for s in st.seeds if s.get("fav")))]
        rows += [(h["id"], h["name"], h["color"], sum(1 for s in st.seeds if h["id"] in s.get("shelves", [])))
                 for h in st.shelves]
        for sid, text, col, n in rows:
            b = _ShelfButton(sid, text, col, n)
            b.setChecked(sid == self.shelf)
            b.clicked.connect(lambda _c=False, k=sid: self.show_shelf(k))
            b.dropped.connect(self._drop_on_shelf)
            if sid not in ("__all", "__fav"):
                b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                b.customContextMenuRequested.connect(lambda pos, k=sid, w=b: self._shelf_menu(k, w.mapToGlobal(pos)))
            self.shelf_box.addWidget(b)

    def _drop_on_shelf(self, shid, seed_id):
        s = store().get(seed_id)
        if s is not None and shid not in s.setdefault("shelves", []):
            s["shelves"].append(shid)
            store().touch(seed=s)
            self._fill_shelves()

    def _shelf_menu(self, shid, at):
        m = QMenu(self)
        a_ren = m.addAction("Rename…")
        a_del = m.addAction("Delete this library (the seeds stay)")
        got = m.exec(at)
        h = store().shelf(shid)
        if h is None:
            return
        if got is a_ren:
            name, ok = QInputDialog.getText(self, "Library", "Name:", text=h["name"])
            if ok and name.strip():
                h["name"] = name.strip()
                store().touch("shelves")
        elif got is a_del:
            if self.shelf == shid:
                self.shelf = "__all"
            store().remove_shelf(shid)

    def _new_shelf(self):
        name, ok = QInputDialog.getText(self, "New library", "Name (e.g. « Mushroom islands », « Speedrun »):")
        if ok and name.strip():
            h = store().add_shelf(name.strip())
            self.show_shelf(h["id"])

    def show_shelf(self, shid):
        self.shelf = shid
        self.refresh()
        motion.fade_in(self.grid_host, 220)

    def _visible(self):
        st = store()
        q = self.search.text().strip().lower()
        out = []
        for s in st.seeds:
            if self.shelf == "__fav" and not s.get("fav"):
                continue
            if self.shelf not in ("__all", "__fav") and self.shelf not in s.get("shelves", []):
                continue
            if q:
                hay = " ".join([label(s), s["seed"], " ".join(s.get("tags") or []), s.get("notes", ""),
                                " ".join(b["data"].get("text", "") + " " + b["data"].get("caption", "")
                                         for b in s.get("board", []))]).lower()
                if not all(w in hay for w in q.split()):
                    continue
            out.append(s)
        k = self.sort.currentIndex()
        if k == 0:
            out.sort(key=lambda s: s.get("modified", ""), reverse=True)
        elif k == 1:
            out.sort(key=lambda s: (not s.get("fav"), s.get("modified", "")[::-1]))
        else:
            out.sort(key=lambda s: label(s).lower())
        return out

    def refresh(self):
        self._fill_shelves()
        st = store()
        name = {"__all": "All seeds", "__fav": "Favourites"}.get(self.shelf)
        if name is None:
            h = st.shelf(self.shelf)
            name = h["name"] if h else "All seeds"
        self.title.setText(name)
        seeds = self._visible()
        self.count.setText(f"{len(seeds)} seed{'s' if len(seeds) != 1 else ''}")
        while self.flow.count():
            it = self.flow.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.cards = {}
        for i, s in enumerate(seeds):
            c = SeedCard(s)
            c.opened.connect(self.open_seed.emit)
            c.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            c.customContextMenuRequested.connect(lambda pos, k=s["id"], w=c: self._card_menu(k, w.mapToGlobal(pos)))
            self.flow.addWidget(c)
            self.cards[s["id"]] = c
            if self.rise_next and i < 24:                     # the opening: they rise one by one
                _rise(c, 0, 18, 420, 120 + 32 * i)
            elif motion.enabled() and i < 24:               # the cards come in one by one
                fx = QGraphicsOpacityEffect(c)
                fx.setOpacity(0.0)
                c.setGraphicsEffect(fx)
                QTimer.singleShot(25 * i, lambda w=c: _fade(w))
        if seeds and not self.search.text():           # (20/11) the last tile: add a seed by hand
            t = AddTile(SeedCard.W, SeedCard.H)
            t.clicked.connect(self.add_seed)
            self.flow.addWidget(t)
            if self.rise_next:
                _rise(t, 0, 18, 420, 120 + 32 * min(len(seeds), 24))
        self.flow.invalidate()                         # (20/11) placed now: back from a seed's page they
        self.flow.activate()                           # all stayed at 0, 0 (same size: no new layout)
        self.rise_next = False
        empty = not seeds
        self.sa.setVisible(not empty)
        self.empty.setVisible(empty)
        if empty:
            self.empty.setText("No seed here yet.\n\nThe ★ next to the seed in the top bar keeps the current one "
                               "— or « Add a seed » above to type one in." if not self.search.text()
                               else "Nothing matches this search.")

    def intro(self):
        """The opening: the side panel slides in, the bar drops, the cards rise."""
        _rise(self.side, -26, 0, 460, 30)
        _rise(self.bar_w, 0, -12, 420, 90)
        if self.empty.isVisible():
            _rise(self.empty, 0, 14, 420, 150)

    def add_seed(self):
        """(20/11) A seed typed in: the dialog, then its card (or the one it already had)."""
        shelf = self.shelf if self.shelf not in ("__all", "__fav") else None
        dlg = AddSeedDialog(self.window(), shelf=shelf, win=getattr(self.window(), "win", None))
        r = dlg.exec()
        sid = dlg.result_id
        if not sid:
            return
        if r == 2:                                     # already there: open it
            self.open_seed.emit(sid)
            return
        self.refresh()
        c = self.cards.get(sid)
        if c is not None:                              # the new card comes in, glowing
            _rise(c, 0, 20, 480, 60)
            QTimer.singleShot(80, lambda: c._hover(1.0))
            QTimer.singleShot(1400, lambda: c._hover(0.0))
            self.sa.ensureWidgetVisible(c)

    def _card_menu(self, sid, at):
        s = store().get(sid)
        if s is None:
            return
        m = QMenu(self)
        a_open = m.addAction("Open")
        a_use = m.addAction("Use this seed")
        a_copy = m.addAction("Copy the seed")
        a_fav = m.addAction("Remove from the favourites" if s.get("fav") else "Add to the favourites")
        a_tag = m.addAction("Add a tag…")
        sub = m.addMenu("Libraries")
        acts = {}
        for h in store().shelves:
            a = sub.addAction(h["name"])
            a.setCheckable(True)
            a.setChecked(h["id"] in s.get("shelves", []))
            acts[a] = h["id"]
        if not store().shelves:
            sub.addAction("(none yet — « New library »)").setEnabled(False)
        m.addSeparator()
        a_del = m.addAction("Delete this seed…")
        got = m.exec(at)
        if got is None:
            return
        if got is a_open:
            self.open_seed.emit(sid)
        elif got is a_use:
            self.window().use_seed(s)
        elif got is a_tag:
            self.open_seed.emit(sid)
            pg = getattr(self.window(), "page", None)
            if pg is not None:
                pg.focus_tag()
        elif got is a_copy:
            QGuiApplication.clipboard().setText(s["seed"])
        elif got is a_fav:
            s["fav"] = not s.get("fav")
            store().touch(seed=s)
        elif got in acts:
            shid = acts[got]
            sh = s.setdefault("shelves", [])
            sh.remove(shid) if shid in sh else sh.append(shid)
            store().touch(seed=s)
            self._fill_shelves()
        elif got is a_del:
            if QMessageBox.question(self, "Delete", f"Delete « {label(s)} » and its board?\n"
                                    "(its pictures go to seed_library/_trash)") == QMessageBox.StandardButton.Yes:
                store().remove_seed(sid)


# ── the board and its blocks ─────────────────────────────────────────────────
class Block(QFrame):
    """A block of the board: chrome (a grip to move it, its tools, ×) shown under the mouse,
    a corner to resize it; its content below."""
    KIND_ICON = {"shot": "image", "map": "map", "note": "note", "coords": "pin"}

    def __init__(self, board, s, b):
        super().__init__(board)
        self.board, self.s, self.b = board, s, b
        self.setObjectName("BoardBlock")
        self.setMouseTracking(True)
        self._hov, self._han = 0.0, None
        self._drag = None                         # ("move" | "size", start mouse, start geometry)
        self.head = QWidget(self)
        hh = QHBoxLayout(self.head)
        hh.setContentsMargins(8, 0, 4, 0)
        hh.setSpacing(2)
        self.grip = QLabel()
        self.grip.setPixmap(icons.pixmap(self.KIND_ICON.get(b["kind"], "more"), 14, theme.TEXT2))
        self.grip.setCursor(Qt.CursorShape.SizeAllCursor)
        hh.addWidget(self.grip)
        hh.addStretch(1)
        self.tools = hh
        self._tool_btns = []
        x = self._tool("close", "Remove this block", self._remove)
        hh.addWidget(x)
        self.head.setFixedHeight(26)
        self.head.setCursor(Qt.CursorShape.SizeAllCursor)
        self.head.installEventFilter(self)
        self.grip.installEventFilter(self)
        self.setGeometry(b["x"], b["y"], b["w"], b["h"])
        self._set_chrome(0.0)

    def _tool(self, ic, tip, fn, checkable=False):
        t = QToolButton(self.head)
        icons.bind(t, ic, 14)
        t.setToolTip(tip)
        t.setAutoRaise(True)
        t.setFixedSize(22, 22)
        t.setCursor(Qt.CursorShape.PointingHandCursor)
        t.clicked.connect(fn)
        self._tool_btns.append(t)
        return t

    def add_tool(self, ic, tip, fn):
        t = self._tool(ic, tip, fn)
        self.tools.insertWidget(self.tools.count() - 1, t)
        return t

    def content_rect(self):
        return QRect(0, 0, self.width(), self.height())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.head.setGeometry(0, 0, self.width(), 26)
        self.head.raise_()

    # chrome under the mouse
    def enterEvent(self, e):
        super().enterEvent(e)
        self._fade_chrome(1.0)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._drag is None:
            self._fade_chrome(0.0)

    def _fade_chrome(self, to):
        if self._han is not None:
            try:
                self._han.stop()
            except RuntimeError:
                pass
        a = self._hov

        def step(t):
            self._set_chrome(a + (to - a) * t)
        if not motion.enabled():
            step(1.0)
            return
        self._han = motion.run(self, 140, step)

    def _set_chrome(self, v):
        self._hov = v
        self.head.setVisible(v > 0.02)
        fx = self.head.graphicsEffect()
        if not isinstance(fx, QGraphicsOpacityEffect):
            fx = QGraphicsOpacityEffect(self.head)
            self.head.setGraphicsEffect(fx)
        fx.setOpacity(v)
        self.update()

    # moving and resizing
    def _corner(self):
        return QRectF(self.width() - 20, self.height() - 20, 20, 20)

    def eventFilter(self, o, e):
        from PySide6.QtCore import QEvent
        if o in (self.head, self.grip):
            if e.type() == QEvent.Type.MouseButtonPress and e.button() == Qt.MouseButton.LeftButton:
                self._start("move", e.globalPosition())
                return True
            if e.type() == QEvent.Type.MouseMove and self._drag is not None:
                self._follow(e.globalPosition())
                return True
            if e.type() == QEvent.Type.MouseButtonRelease and self._drag is not None:
                self._end()
                return True
        return False

    def mousePressEvent(self, e):
        self.raise_()
        self.board.raised(self)
        if e.button() == Qt.MouseButton.LeftButton and self._corner().contains(e.position()):
            self._start("size", e.globalPosition())
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self._follow(e.globalPosition())
            return
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if self._corner().contains(e.position())
                       else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._end()
            return
        super().mouseReleaseEvent(e)

    def _start(self, kind, gpos):
        self.raise_()
        self.board.raised(self)
        self._drag = (kind, gpos, QRect(self.geometry()))
        fx = QGraphicsDropShadowEffect(self)              # lifted while it moves
        fx.setBlurRadius(34)
        fx.setOffset(0, 10)
        fx.setColor(QColor(0, 0, 0, 140))
        self.setGraphicsEffect(fx)

    def _follow(self, gpos):
        kind, p0, g0 = self._drag
        d = gpos - p0
        if kind == "move":
            x = max(0, round((g0.x() + d.x()) / GRID) * GRID)
            y = max(0, round((g0.y() + d.y()) / GRID) * GRID)
            self.move(int(x), int(y))
        else:
            mw, mh = (160, 120)
            w = max(mw, round((g0.width() + d.x()) / GRID) * GRID)
            h = max(mh, round((g0.height() + d.y()) / GRID) * GRID)
            self.resize(int(w), int(h))
        self.board.grow()

    def _end(self):
        self._drag = None
        self.setGraphicsEffect(None)
        g = self.geometry()
        self.b.update(x=g.x(), y=g.y(), w=g.width(), h=g.height())
        store().touch(seed=self.s)
        self.saved()
        if not self.underMouse():
            self._fade_chrome(0.0)

    def saved(self):
        """After a move / resize (the map re-renders at its new size)."""

    def _remove(self):
        self.board.remove_block(self)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bg = QColor(theme.BG2)
        bg.setAlphaF(0.96)
        p.setPen(QPen(motion.mix(theme.LINE, GOLD, 0.45 * self._hov), 1))
        p.setBrush(bg)
        p.drawRoundedRect(r, 14, 14)
        self.paint_content(p, r)
        if self._hov > 0.02:                                  # the head band, the corner
            hb = QColor(10, 14, 22)
            hb.setAlphaF(0.55 * self._hov)
            path = QPainterPath()
            path.addRoundedRect(QRectF(r.left(), r.top(), r.width(), 40), 14, 14)
            p.save()
            p.setClipRect(QRectF(r.left(), r.top(), r.width(), 26))
            p.fillPath(path, hb)
            p.restore()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, int(150 * self._hov)))
            for i in range(3):
                for j in range(3 - i):
                    p.drawEllipse(QPointF(r.right() - 6 - i * 4.5, r.bottom() - 6 - j * 4.5), 1.1, 1.1)

    def paint_content(self, p, r):
        pass


class ShotBlock(Block):
    """A screenshot: filled into the block (or whole: « fit »), a caption over its foot."""

    def __init__(self, board, s, b):
        super().__init__(board, s, b)
        self.img = QImage(image_path(b["id"]))
        self._pm = None
        self.add_tool("image", "Fill / whole picture", self._toggle_fit)
        self.cap = QLineEdit(self)
        self.cap.setObjectName("BlockCaption")
        self.cap.setPlaceholderText("Caption…")
        self.cap.setText(b["data"].get("caption", ""))
        self.cap.editingFinished.connect(self._caption)
        self._place_cap()

    def _toggle_fit(self):
        self.b["data"]["fit"] = "contain" if self.b["data"].get("fit", "cover") == "cover" else "cover"
        store().touch(seed=self.s)
        self._pm = None
        self.update()

    def _caption(self):
        self.b["data"]["caption"] = self.cap.text()
        store().touch(seed=self.s)

    def _place_cap(self):
        self.cap.setGeometry(10, self.height() - 34, self.width() - 34, 26)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._pm = None
        self._place_cap()

    def mouseDoubleClickEvent(self, e):
        if not self.img.isNull():
            ImageViewer(self.img, self.cap.text(), self.window()).exec()

    def paint_content(self, p, r):
        if self.img.isNull():
            p.setPen(QColor(theme.TEXT3))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Picture missing")
            return
        if self._pm is None or self._pm.size() != QSize(int(r.width() * 2), int(r.height() * 2)):
            self._pm = _rounded(self.img, r.width(), r.height(), 14, self.b["data"].get("fit", "cover"))
        p.drawPixmap(r, self._pm, QRectF(self._pm.rect()))
        foot = QLinearGradient(0, r.bottom() - 50, 0, r.bottom())
        foot.setColorAt(0, QColor(0, 0, 0, 0))
        foot.setColorAt(1, QColor(0, 0, 0, 150))
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        p.save()
        p.setClipPath(path)
        p.fillRect(QRectF(r.left(), r.bottom() - 50, r.width(), 50), foot)
        p.restore()


# ── mini-maps: zoom, noise keys (20/11) ─────────────────────────────────────
# the zoom is free (blocks per screen pixel, these steps); the biomes are generated at the
# nearest scale cubiomes has (1, 4, 16, 64, 256) and drawn stretched to the zoom
ZOOMS = (0.25, 0.5, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256)
GEN_SCALES = (1, 4, 16, 64, 256)
STRUCT_MAX_BPP = 24.0
_NOISE_UI = {}                       # NoiseMapLib of each (seed, large, noise), for the octave lists                # structures up to there (strongholds: at every zoom)


def zoom_label(bpp):
    bpp = float(bpp)
    return f"1:{bpp:g}" if bpp >= 1 else f"{1 / bpp:g}:1"


def map_bpp(d):
    """Blocks per screen pixel of a mini-map (older ones kept a cell scale, drawn 2 px a cell)."""
    b = d.get("bpp")
    if b is None:
        b = int(d.get("scale", 4) or 4) / 2.0
    return max(ZOOMS[0], min(ZOOMS[-1], float(b)))


def zoom_step(bpp, steps):
    i = min(range(len(ZOOMS)), key=lambda j: abs(math.log(ZOOMS[j]) - math.log(bpp)))
    return ZOOMS[max(0, min(len(ZOOMS) - 1, i + steps))]


def view_cells(bpp, wpx, hpx):
    """(generation scale, cells wide, cells high) of a picture covering wpx × hpx pixels."""
    s = max(x for x in GEN_SCALES if x <= max(1.0, 2.0 * bpp))
    return s, int(wpx * bpp / s) + 3, int(hpx * bpp / s) + 3


def map_structs(d):
    keys = list(d.get("structs") or [])
    if map_bpp(d) > STRUCT_MAX_BPP:
        keys = [k for k in keys if k == "stronghold"]
    return keys


def octave_rows(wkey, noise):
    """[(pair, incl_b, label)] of a climate noise's octaves, in the map's order (0A, 0B, 1A…)."""
    from .map_engine import CLIMATE
    if noise not in CLIMATE:
        return []
    k = (wkey[0], wkey[2], noise)
    nm = _NOISE_UI.get(k)
    if nm is None:
        import cubiomes_wrap as cw
        nm = _NOISE_UI[k] = cw.NoiseMapLib(wkey[0], nptype=cw.NOISE_PARAMS[noise], large=wkey[2])
    try:
        info = nm.octave_info()
    except Exception:                                    # noqa: BLE001
        info = None
    if not info:
        return []
    a, b = info
    total = sum(abs(x) for x in a) + sum(abs(x) for x in b) or 1.0
    out = []
    for i in range(max(len(a), len(b))):
        for stream, amps in (("A", a), ("B", b)):
            if i < len(amps):
                out.append((i, stream == "B", f"{i}{stream}   {abs(amps[i]) / total * 100:.1f} %"))
    return out


def noise_key(d, name, wkey):
    """Alt+T / H / C / E / W / Y / B on a mini-map's data → the message to show."""
    from .layers_panel import NOISE_KEYS
    if name == "Exact Y":
        return "Exact Y: on the map only (too slow for a mini-map)"
    if name == "Off":
        d["noise"] = None
        return f"Biomes   ·   {NOISE_KEYS['Off']}"
    if not noise_ok(name, wkey[1]):
        return "Noise layers exist from Java 1.18 (noise-based generation)"
    if d.get("noise") != name:
        d["octave"] = None
    d["noise"] = name
    return f"{name}   ·   {NOISE_KEYS[name]}"


def octave_key(d, k, upto, wkey):
    """Alt+1…9 one octave, Alt+Shift+1…9 up to it, Alt+0 all of them."""
    from .map_engine import CLIMATE
    if d.get("noise") not in CLIMATE:
        return "Octaves: choose a climate noise first (Alt+T / H / C / E / W)"
    if k == 0:
        d["octave"] = None
        return f"{d['noise']}: all octaves   ·   Alt+1…9 one octave, Alt+Shift+1…9 up to it"
    rows = octave_rows(wkey, d["noise"])
    if not rows:
        return "Octave data needs a newer libcubiomes"
    if k > len(rows):
        return f"{d['noise']} has {len(rows)} octave streams (Alt+1 … Alt+{min(9, len(rows))})"
    pair, inclb, lab = rows[k - 1]
    d["octave"] = [pair, inclb, not upto]
    return f"{d['noise']}: {'up to' if upto else 'only'} octave {lab.split()[0]}   ·   Alt+0: all octaves"


def octave_mode_key(d, wkey):
    """Alt+O: the same octave, Up to ↔ Only."""
    from .map_engine import CLIMATE
    if d.get("noise") not in CLIMATE:
        return "Octaves: choose a climate noise first (Alt+T / H / C / E / W)"
    o = d.get("octave")
    if not o:
        rows = octave_rows(wkey, d["noise"])
        if not rows:
            return "Octave data needs a newer libcubiomes"
        o = [rows[0][0], rows[0][1], False]
    o = [o[0], o[1], not o[2]]
    d["octave"] = o
    return f"{d['noise']}: {'only' if o[2] else 'up to'} octave {o[0]}{'B' if o[1] else 'A'}   ·   Alt+O to switch"


def octave_label(d):
    o = d.get("octave")
    if not o or not d.get("noise"):
        return ""
    return f"{'only' if o[2] else '≤'} {o[0]}{'B' if o[1] else 'A'}"


def install_noise_keys(owner, fn):
    """The map's noise shortcuts on a window: fn(kind, *args) with kind "noise" (name),
    "octave" (k, upto) or "mode"."""
    from PySide6.QtGui import QKeySequence, QShortcut
    from .layers_panel import NOISE_KEYS
    for name, key in NOISE_KEYS.items():
        QShortcut(QKeySequence(key), owner, activated=lambda n=name: fn("noise", n))
    for k in range(10):
        QShortcut(QKeySequence(f"Alt+{k}"), owner, activated=lambda k=k: fn("octave", k, False))
        if k:
            QShortcut(QKeySequence(f"Alt+Shift+{k}"), owner, activated=lambda k=k: fn("octave", k, True))
    QShortcut(QKeySequence("Alt+O"), owner, activated=lambda: fn("mode"))


def apply_noise_key(d, wkey, kind, *a):
    if kind == "noise":
        return noise_key(d, a[0], wkey)
    if kind == "octave":
        return octave_key(d, a[0], a[1], wkey)
    return octave_mode_key(d, wkey)


class Toast(QLabel):
    """A short message at the bottom of a window (a noise key's answer…), fading out."""

    def __init__(self, host):
        super().__init__(host)
        self.setObjectName("LibToast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()
        self._fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fx)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.timeout.connect(self._out)

    def say(self, text):
        self.setText(text)
        self.adjustSize()
        h = self.parentWidget()
        self.move((h.width() - self.width()) // 2, h.height() - self.height() - 28)
        self.show()
        self.raise_()
        self._fx.setOpacity(1.0)
        self._t.start(2200)

    def _out(self):
        motion.run(self, 380, lambda t: self._fx.setOpacity(1.0 - t), self.hide)


class MapBlock(Block):
    """A mini-map of the seed: its biomes (or a noise map), the spawn and the structures.
    Live: drag to move, wheel to zoom; frozen (the lock): a still picture of a place."""

    def __init__(self, board, s, b):
        super().__init__(board, s, b)
        d = b["data"]
        d.setdefault("x", None)
        d.setdefault("z", None)
        d["bpp"] = map_bpp(d)                       # (20/11) a free zoom (was: a cell scale)
        d.setdefault("frozen", False)
        d.setdefault("structs", list(MAP_STRUCTS))
        d.setdefault("spawn", True)
        d.setdefault("origin", False)
        d.setdefault("noise", None)                 # (19/11 ter) a noise map instead of the biomes
        d.setdefault("npal", None)
        d.setdefault("nalpha", 1.0)
        d.setdefault("octave", None)
        self.img, self.info = None, {}
        self._pan = None
        self._tok = 0
        self.lockb = self.add_tool("lock" if d["frozen"] else "unlock",
                                   "Frozen: a still picture (click to unfreeze)" if d["frozen"]
                                   else "Live: drag to move, wheel to zoom (click to freeze)", self._toggle_lock)
        self.add_tool("edit", "Place, zoom, colours and structures", self.customize)
        self.add_tool("map", "Open the map here", self._open_map)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(140)
        self._t.timeout.connect(self._request)
        renderer().done.connect(self._in)
        self._request()

    def _toggle_lock(self):
        d = self.b["data"]
        d["frozen"] = not d["frozen"]
        icons.bind(self.lockb, "lock" if d["frozen"] else "unlock", 14)
        self.lockb.setToolTip("Frozen: a still picture (click to unfreeze)" if d["frozen"]
                              else "Live: drag to move, wheel to zoom (click to freeze)")
        store().touch(seed=self.s)
        self.update()

    def customize(self):
        """(19/11 bis) The mini-map editor: its place, zoom, colours and structures."""
        self.board.current_map = self
        ed = MapEditor(self.s, self.b["data"], self.window())
        if ed.exec():
            self.b["data"].update(ed.data())
            d = self.b["data"]
            icons.bind(self.lockb, "lock" if d["frozen"] else "unlock", 14)
            store().touch(seed=self.s)
            self._request()

    def mouseDoubleClickEvent(self, e):
        if e.position().y() > 26:
            self.customize()
            return
        super().mouseDoubleClickEvent(e)

    def enterEvent(self, e):
        super().enterEvent(e)
        self.board.current_map = self               # the noise keys go to the map under the mouse

    def _open_map(self):
        d = self.b["data"]
        win = self.window()
        if hasattr(win, "use_seed"):
            win.use_seed(self.s, at=(d.get("x") or 0, d.get("z") or 0, map_bpp(d)))

    def saved(self):
        self._request()

    def noise_key(self, kind, *a):
        """A noise shortcut on this mini-map → its message."""
        msg = apply_noise_key(self.b["data"], world_key(self.s), kind, *a)
        store().touch(seed=self.s)
        self._request()
        return msg

    def _request(self):
        d = self.b["data"]
        self._tok += 1
        sc, w, h = view_cells(map_bpp(d), self.width(), self.height())
        renderer().request(("map", self.b["id"]), self._tok, world_key(self.s), d["x"] or 0, d["z"] or 0, sc,
                           w, h, structs=map_structs(d), around_spawn=d["x"] is None,
                           noise=map_noise(d, getattr(self.window(), "win", None)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.img is not None:
            self._t.start()                         # a bigger block: more of the map

    def _in(self, owner, tok, img, info):
        if owner == ("spawn", world_key(self.s)):          # the spawn found since: drawn
            self._request()
            return
        if owner != ("map", self.b["id"]):
            return
        try:
            if tok != self._tok or img is None:
                return
            if self.b["data"]["x"] is None and info.get("spawn"):
                self.b["data"]["x"], self.b["data"]["z"] = info["spawn"]
            self.img, self.info = img, info
            self._pan = None
            self.update()
            self._save_cover()
            self.board.cover_changed(self)
        except RuntimeError:
            pass

    def _save_cover(self):
        """(20/11) The seed's cover (its card, its page, the home page) is the first mini-map
        of its board as it shows — unless the board has a screenshot."""
        if cover_map(self.s) is self.b and self.img is not None:
            save_map_cover(self.s, self.b, self.img, self.info)

    def _bpp(self):
        return map_bpp(self.b["data"])

    def mousePressEvent(self, e):
        self.board.current_map = self
        if e.button() == Qt.MouseButton.LeftButton and not self.b["data"]["frozen"] \
                and not self._corner().contains(e.position()) and e.position().y() > 26:
            self.raise_()
            self.board.raised(self)
            self._pan = (e.position(), self.b["data"]["x"] or 0, self.b["data"]["z"] or 0)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._pan is not None and self._drag is None:
            p0, x0, z0 = self._pan
            d = e.position() - p0
            self.b["data"]["x"] = int(x0 - d.x() * self._bpp())
            self.b["data"]["z"] = int(z0 - d.y() * self._bpp())
            self.update()
            self._t.start()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._pan is not None and self._drag is None:
            self._pan = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            store().touch(seed=self.s)
            self._request()
            return
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        if self.b["data"]["frozen"]:
            e.ignore()
            return
        d = self.b["data"]
        old = map_bpp(d)
        new = zoom_step(old, -1 if e.angleDelta().y() > 0 else 1)
        if new != old and d["x"] is not None:
            pos = e.position()                              # the point under the mouse stays
            c = QRectF(self.rect()).center()
            wx = d["x"] + (pos.x() - c.x()) * old
            wz = d["z"] + (pos.y() - c.y()) * old
            d["x"] = int(wx - (pos.x() - c.x()) * new)
            d["z"] = int(wz - (pos.y() - c.y()) * new)
            d["bpp"] = new
            self.update()
            self._t.start()
            store().touch(seed=self.s)
        e.accept()

    def paint_content(self, p, r):
        d = self.b["data"]
        paint_map(p, r, self.img, self.info, d["x"] or 0, d["z"] or 0, map_bpp(d), d.get("spawn", True), 14,
                  show_origin=d.get("origin"))
        ol = octave_label(d)
        tag = f"{d.get('x') or 0}, {d.get('z') or 0}  ·  {zoom_label(map_bpp(d))}" + \
            (f"  ·  {d['noise']}" + (f" {ol}" if ol else "") if d.get("noise") else "") + \
            ("  ·  frozen" if d["frozen"] else "")
        map_chip(p, QPointF(r.left() + 8, r.bottom() - 26), tag)


def cover_map(s):
    """The mini-map that is the seed's cover: the first of its board (None: a screenshot leads)."""
    if any(b["kind"] == "shot" and os.path.isfile(image_path(b["id"])) for b in s.get("board", [])):
        return None
    return next((b for b in s.get("board", []) if b["kind"] == "map"), None)


def cover_key(b):
    import json
    return f"{b['id']}:{b['w']}x{b['h']}:" + json.dumps(b["data"], sort_keys=True, default=str)


def save_map_cover(s, b, img, info):
    """The mini-map b as it shows (its picture, icons) saved as the seed's cover."""
    d = b["data"]
    W, H = max(64, int(b["w"])), max(48, int(b["h"]))
    im = QImage(W * 2, H * 2, QImage.Format.Format_RGB32)
    im.fill(QColor(theme.BG3))
    p = QPainter(im)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(2, 2)
    cx = d.get("x") if d.get("x") is not None else info.get("cx", 0)
    cz = d.get("z") if d.get("z") is not None else info.get("cz", 0)
    paint_map(p, QRectF(0, 0, W, H), img, info, cx, cz, map_bpp(d), d.get("spawn", True), 0,
              show_origin=d.get("origin"))
    p.end()
    try:
        im.save(thumb_path(s["id"]))
        s["cover"] = cover_key(b)               # (the cover shows this mini-map as it is now)
        store()._save_t.start()
        renderer().cover_saved.emit(s["id"])
    except Exception:                            # noqa: BLE001
        pass
    return im


def map_noise(d, win=None):
    """The noise argument of a mini-map's request (None: the biomes)."""
    if not d.get("noise"):
        return None
    o = d.get("octave")
    return (d["noise"], d.get("npal") or default_noise_palette(win), float(d.get("nalpha", 1.0)),
            tuple(o) if o else None)


def map_chip(p, at, text):
    p.save()
    p.setFont(theme.num_font(7.8))
    fm = p.fontMetrics()
    tr = QRectF(at.x(), at.y(), fm.horizontalAdvance(text) + 16, 18)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(10, 14, 22, 170))
    p.drawRoundedRect(tr, 9, 9)
    p.setPen(QColor("#ffffff"))
    p.drawText(tr, Qt.AlignmentFlag.AlignCenter, text)
    p.restore()


def paint_map(p, r, img, info, cx, cz, bpp, show_spawn=True, radius=14, icon=16, hot=None,
              show_origin=False):
    """A mini-map in r (rounded) centred on (cx, cz) at bpp blocks per pixel: its picture (made
    for this place or near it: it follows), its structures, the spawn, the origin.
    Returns [(QRectF, key, x, z)] of the icons drawn (for a mouse over them)."""
    hits = []
    path = QPainterPath()
    path.addRoundedRect(r, radius, radius)
    p.save()
    p.setClipPath(path)
    p.fillPath(path, QColor(theme.BG3))
    if img is not None and not img.isNull() and info.get("scale"):
        c = r.center()

        def at(bx, bz):
            return (c.x() + (bx - cx) / bpp, c.y() + (bz - cz) / bpp)
        k = info["scale"] / bpp                              # pixels per cell
        x, y = at(info["x0"], info["z0"])
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, k < 1.0)
        p.drawImage(QRectF(x, y, img.width() * k, img.height() * k), img)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        from . import struct_icons
        for key, bx, bz in info.get("structs") or ():
            if key != "stronghold" and bpp > STRUCT_MAX_BPP:
                continue
            sx, sy = at(bx, bz)
            if r.left() - 10 < sx < r.right() + 10 and r.top() - 10 < sy < r.bottom() + 10:
                big = icon + (6 if hot is not None and hot[1:] == (key, bx, bz) else 0)
                ic = struct_icons.pixmap(key, big)
                rr = QRectF(sx - big / 2, sy - big / 2, big, big)
                p.drawPixmap(rr, ic, QRectF(ic.rect()))
                hits.append((rr, key, bx, bz))
        if show_origin:                                  # (19/11 ter) 0, 0
            sx, sy = at(0, 0)
            if r.left() - 12 < sx < r.right() + 12 and r.top() - 12 < sy < r.bottom() + 12:
                big = icon + 2
                ic = struct_icons.pixmap("origin", big)
                rr = QRectF(sx - big / 2, sy - big / 2, big, big)
                p.drawPixmap(rr, ic, QRectF(ic.rect()))
                hits.append((rr, "origin", 0, 0))
        sp = info.get("spawn")
        if sp and show_spawn:
            sx, sy = at(*sp)
            big = icon + 4
            ic = struct_icons.pixmap("spawn", big)
            rr = QRectF(sx - big / 2, sy - big + 2, big, big)
            p.drawPixmap(rr, ic, QRectF(ic.rect()))
            hits.append((rr, "spawn", sp[0], sp[1]))
    else:
        p.setPen(QColor(theme.TEXT3))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, "Drawing the map…")
    p.restore()
    return hits


NOTE_COLORS = ("#f5d76e", "#8fd6a0", "#8ec5ff", "#f4a7c8", "#c8b4ff", "#e8ecf3")


class NoteBlock(Block):
    """A note on paper of a soft colour (the dots of its head change it)."""

    def __init__(self, board, s, b):
        super().__init__(board, s, b)
        b["data"].setdefault("color", NOTE_COLORS[0])
        self.ed = QPlainTextEdit(self)
        self.ed.setObjectName("NoteEdit")
        self.ed.setPlaceholderText("Write here…")
        self.ed.setPlainText(b["data"].get("text", ""))
        self.ed.textChanged.connect(self._text)
        self.ed.setFrameShape(QFrame.Shape.NoFrame)
        for c in NOTE_COLORS:
            t = QToolButton(self.head)
            t.setFixedSize(16, 16)
            t.setCursor(Qt.CursorShape.PointingHandCursor)
            t.setStyleSheet(f"QToolButton {{ background: {c}; border-radius: 8px; border: 1px solid rgba(0,0,0,0.25); }}")
            t.clicked.connect(lambda _c=False, col=c: self._color(col))
            self.tools.insertWidget(self.tools.count() - 1, t)
        self._style()

    def _style(self):
        self.ed.setStyleSheet("QPlainTextEdit#NoteEdit { background: transparent; border: 0; color: #2a2a30;"
                              " font-size: 10pt; }")
        self.update()

    def _color(self, c):
        self.b["data"]["color"] = c
        store().touch(seed=self.s)
        self.update()

    def _text(self):
        self.b["data"]["text"] = self.ed.toPlainText()
        store().touch(seed=self.s)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.ed.setGeometry(12, 28, self.width() - 24, self.height() - 40)

    def paint_content(self, p, r):
        c = QColor(self.b["data"].get("color", NOTE_COLORS[0]))
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0, c.lighter(108))
        g.setColorAt(1, c)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, 14, 14)
        fold = QPainterPath()                                     # a folded corner
        fold.moveTo(r.right() - 22, r.bottom())
        fold.lineTo(r.right(), r.bottom() - 22)
        fold.lineTo(r.right(), r.bottom() - 8)
        fold.quadTo(r.right(), r.bottom(), r.right() - 8, r.bottom())
        fold.closeSubpath()
        p.setBrush(c.darker(118))
        p.drawPath(fold)


class CoordsBlock(Block):
    """Places of the seed: a name and X Y Z per line; a click copies its /tp."""

    def __init__(self, board, s, b):
        super().__init__(board, s, b)
        b["data"].setdefault("points", [dict(name="Spawn", x="", y="", z="")])
        self.add_tool("plus", "Add a place", self._add)
        self.host = QWidget(self)
        self.lay = QVBoxLayout(self.host)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(4)
        self.sa = QScrollArea(self)
        self.sa.setWidgetResizable(True)
        self.sa.setFrameShape(QFrame.Shape.NoFrame)
        self.sa.setWidget(self.host)
        self.sa.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._fill()

    def _fill(self):
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, pt in enumerate(self.b["data"]["points"]):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(4)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {NOTE_COLORS[i % len(NOTE_COLORS)]}; border-radius: 5px;")
            h.addWidget(dot)
            n = QLineEdit(pt.get("name", ""))
            n.setPlaceholderText("Place")
            n.setObjectName("FlatEdit")
            n.setMinimumWidth(60)
            n.editingFinished.connect(lambda i=i, w=n: self._set(i, "name", w.text()))
            h.addWidget(n, 2)
            for k in ("x", "y", "z"):
                e = QLineEdit(str(pt.get(k, "")))
                e.setPlaceholderText(k.upper())
                e.setObjectName("FlatEdit")
                e.setFont(theme.mono_font(8.6))
                e.setFixedWidth(54)
                e.editingFinished.connect(lambda i=i, k=k, w=e: self._set(i, k, w.text()))
                h.addWidget(e)
            cp = QToolButton()
            icons.bind(cp, "copy", 14)
            cp.setAutoRaise(True)
            cp.setToolTip("Copy its /tp")
            cp.clicked.connect(lambda _c=False, i=i: self._copy(i))
            h.addWidget(cp)
            self.lay.addWidget(row)
        self.lay.addStretch(1)

    def _set(self, i, k, v):
        self.b["data"]["points"][i][k] = v.strip()
        store().touch(seed=self.s)

    def _add(self):
        self.b["data"]["points"].append(dict(name="", x="", y="", z=""))
        store().touch(seed=self.s)
        self._fill()

    def _copy(self, i):
        pt = self.b["data"]["points"][i]
        x, y, z = (pt.get(k) or "~" for k in ("x", "y", "z"))
        QGuiApplication.clipboard().setText(f"/tp @s {x} {y} {z}")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.sa.setGeometry(12, 30, self.width() - 24, self.height() - 42)

    def paint_content(self, p, r):
        p.setPen(QColor(theme.TEXT3))
        p.setFont(theme.ui_font(7.5, QFont.Weight.Bold))
        p.drawText(QRectF(r.left() + 14, r.top() + 8, 120, 16), Qt.AlignmentFlag.AlignVCenter,
                   "PLACES" if self._hov < 0.5 else "")


KINDS = {"shot": ShotBlock, "map": MapBlock, "note": NoteBlock, "coords": CoordsBlock}


class Board(QWidget):
    """The free board of a seed: a dotted canvas that grows with its blocks; pictures can be
    dropped or pasted on it."""
    changed_cover = Signal()
    current_map = None                               # (20/11) the mini-map the noise keys go to

    def __init__(self, page):
        super().__init__()
        self.page = page
        self.s = None
        self.blocks = []
        self.setAcceptDrops(True)
        self.setMinimumSize(800, 600)

    def load(self, s):
        for b in self.blocks:
            b.deleteLater()
        self.blocks = []
        self.current_map = None
        self.s = s
        for b in sorted(s.get("board", []), key=lambda b: b.get("z", 0)):
            self._make(b)
        self.grow()

    def _make(self, b, animate=False):
        cls = KINDS.get(b["kind"])
        if cls is None:
            return None
        w = cls(self, self.s, b)
        w.show()
        self.blocks.append(w)
        if animate and motion.enabled():               # it lands: a fade and a small growth
            g = QRect(b["x"], b["y"], b["w"], b["h"])
            fx = QGraphicsOpacityEffect(w)
            fx.setOpacity(0.0)
            w.setGraphicsEffect(fx)

            def step(t):
                k = 0.9 + 0.1 * t
                w.setGeometry(int(g.center().x() - g.width() * k / 2), int(g.center().y() - g.height() * k / 2),
                              int(g.width() * k), int(g.height() * k))
                fx.setOpacity(t)

            def done():
                w.setGraphicsEffect(None)
                w.setGeometry(g)
            motion.run(w, 260, step, done)
        return w

    def raised(self, w):
        z = max([b.b.get("z", 0) for b in self.blocks] + [0]) + 1
        if w.b.get("z") != z:
            w.b["z"] = z

    def grow(self):
        r = QRect()
        for b in self.blocks:
            r = r.united(b.geometry())
        vp = self.page.scroll.viewport().size()
        self.setMinimumSize(max(vp.width(), r.right() + 240), max(vp.height(), r.bottom() + 240))

    def free_spot(self, w, h):
        """A place for a new block: the first free one, reading the board line by line."""
        vis = self.page.scroll.viewport()
        top = self.page.scroll.verticalScrollBar().value()
        width = max(600, vis.width() - 40)
        for y in range(top + 24, top + 4000, GRID * 3):
            for x in range(24, width - w, GRID * 3):
                rr = QRect(x, y, w, h).adjusted(-12, -12, 12, 12)
                if not any(b.geometry().intersects(rr) for b in self.blocks):
                    return x, y
        return 24, top + 24

    def add(self, kind, at=None, data=None):
        w, h = BLOCK_SIZES[kind]
        x, y = at if at is not None else self.free_spot(w, h)
        b = store().add_block(self.s, kind, round(x / GRID) * GRID, round(y / GRID) * GRID, w=w, h=h, data=data)
        blk = self._make(b, animate=True)
        self.grow()
        if blk is not None:
            self.page.scroll.ensureVisible(b["x"] + b["w"] // 2, b["y"] + b["h"] // 2, b["w"] // 2 + 20, b["h"] // 2 + 20)
        return blk

    def add_image(self, img, at=None, caption=""):
        if img is None or img.isNull():
            return
        from .seed_store import new_id
        bid = new_id()
        img.save(image_path(bid), "PNG")
        w = 360
        h = max(160, min(480, int(w * img.height() / max(1, img.width()))))
        x, y = at if at is not None else self.free_spot(w, h)
        b = dict(id=bid, kind="shot", x=round(x / GRID) * GRID, y=round(y / GRID) * GRID, w=w, h=h,
                 z=max([bb.get("z", 0) for bb in self.s["board"]] + [0]) + 1, data=dict(caption=caption))
        self.s["board"].append(b)
        store().touch(seed=self.s)
        self._make(b, animate=True)
        self.grow()
        self.cover_changed(None)

    def remove_block(self, w):
        bid = w.b["id"]

        def gone():
            try:
                w.deleteLater()
            except RuntimeError:
                pass
        if w in self.blocks:
            self.blocks.remove(w)
        store().remove_block(self.s, bid)
        if motion.enabled():
            fx = QGraphicsOpacityEffect(w)
            w.setGraphicsEffect(fx)
            motion.run(w, 180, lambda t: fx.setOpacity(1 - t), gone)
        else:
            gone()
        self.cover_changed(None)

    def cover_changed(self, w):
        self.changed_cover.emit()

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasImage() or (md.hasUrls() and any(u.isLocalFile() for u in md.urls())):
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        pos = e.position().toPoint()
        if md.hasImage():
            self.add_image(QImage(md.imageData()), (pos.x(), pos.y()))
            return
        for i, u in enumerate(md.urls()):
            if u.isLocalFile():
                img = QImage(u.toLocalFile())
                if not img.isNull():
                    self.add_image(img, (pos.x() + i * 24, pos.y() + i * 24),
                                   caption=os.path.splitext(os.path.basename(u.toLocalFile()))[0])

    def paintEvent(self, e):
        p = QPainter(self)
        dot = QColor(theme.TEXT)
        dot.setAlphaF(0.07)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        r = e.rect()
        step = GRID * 3
        for y in range(r.top() - r.top() % step, r.bottom() + step, step):
            for x in range(r.left() - r.left() % step, r.right() + step, step):
                p.drawRect(QRectF(x, y, 1.6, 1.6))
        if not self.blocks:
            p.setPen(QColor(theme.TEXT3))
            p.setFont(theme.ui_font(10))
            p.drawText(QRectF(0, 80, self.width(), 60), Qt.AlignmentFlag.AlignHCenter,
                       "An empty board.\nAdd a screenshot, a mini-map, a note or places above — "
                       "or drop pictures here (Ctrl+V pastes one).")


class _SeedHead(QFrame):
    """The seed's header: its world (the cover) shows through on the right, under a veil."""

    def __init__(self):
        super().__init__()
        self._img, self._pm = None, None

    def set_art(self, img):
        self._img, self._pm = img, None
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._pm = None

    def paintEvent(self, e):
        super().paintEvent(e)
        if self._img is None or self._img.isNull():
            return
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        if self._pm is None:
            w, h = int(r.width() * 0.62), int(r.height())
            pm = QPixmap(w * 2, h * 2)
            pm.setDevicePixelRatio(2.0)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            iw, ih = self._img.width(), self._img.height()
            k = max(w / iw, h / ih)
            p.drawImage(QRectF((w - iw * k) / 2, (h - ih * k) / 2, iw * k, ih * k), self._img)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            g = QLinearGradient(0, 0, w, 0)                      # it fades in from the left
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(0.55, QColor(0, 0, 0, 70))
            g.setColorAt(1.0, QColor(0, 0, 0, 120))
            p.fillRect(QRectF(0, 0, w, h), g)
            p.end()
            self._pm = pm
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(r, 18, 18)
        p.setClipPath(path)
        w = self._pm.width() / 2
        p.drawPixmap(QRectF(r.right() - w, r.top(), w, r.height()), self._pm, QRectF(self._pm.rect()))


class _TagChip(QPushButton):
    def __init__(self, text):
        super().__init__(text + "  ×")
        self.setObjectName("TagChip")
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class SeedPage(QWidget):
    """One seed: its header, the tools to add blocks, its board."""
    back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.s = None
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)
        self.head = _SeedHead()
        self.head.setObjectName("SeedHead")
        hv = QHBoxLayout(self.head)
        hv.setContentsMargins(18, 16, 18, 16)
        hv.setSpacing(16)
        bk = QToolButton()
        icons.bind(bk, "back", 20)
        bk.setToolTip("Back to the library  (Esc)")
        bk.setFixedSize(34, 34)
        bk.clicked.connect(self.back.emit)
        hv.addWidget(bk, 0, Qt.AlignmentFlag.AlignTop)
        self.sg = QLabel()
        self.sg.setFixedSize(76, 76)
        hv.addWidget(self.sg, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.name = QLineEdit()
        self.name.setObjectName("SeedName")
        self.name.setPlaceholderText("Name this seed…")
        self.name.editingFinished.connect(self._rename)
        col.addWidget(self.name)
        nr = QHBoxLayout()
        nr.setSpacing(8)
        self.num = QLabel()
        self.num.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        nr.addWidget(self.num)
        cp = QToolButton()
        icons.bind(cp, "copy", 16)
        cp.setToolTip("Copy the seed")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.s["seed"]))
        nr.addWidget(cp)
        self.c_ver = QLabel()
        self.c_ver.setObjectName("Chip")
        self.c_ver.setProperty("state", "off")
        nr.addWidget(self.c_ver)
        nr.addStretch(1)
        col.addLayout(nr)
        self.tags_host = QWidget()
        self.tags = FlowLayout(self.tags_host, 6, 6)
        col.addWidget(self.tags_host)
        hv.addLayout(col, 1)
        right = QVBoxLayout()
        right.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.fav = QToolButton()
        self.fav.setFixedSize(38, 38)
        self.fav.setIconSize(QSize(24, 24))
        self.fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fav.clicked.connect(self._toggle_fav)
        top.addWidget(self.fav)
        self.shelves_b = QPushButton("  Libraries")
        self.shelves_b.setProperty("kind", "ghost")
        icons.bind(self.shelves_b, "seed_library", 16)
        self.shelves_b.clicked.connect(self._shelves_menu)
        top.addWidget(self.shelves_b)
        use = QPushButton("  Use this seed")
        use.setProperty("kind", "primary")
        icons.bind(use, "check", 16, "on_accent", "on_accent")
        use.clicked.connect(lambda: self.window().use_seed(self.s))
        self._use_shine = motion.Shine(use)
        top.addWidget(use)
        more = QToolButton()
        icons.bind(more, "more", 18)
        more.setFixedSize(34, 34)
        more.clicked.connect(self._more)
        top.addWidget(more)
        right.addLayout(top)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addStretch(1)
        for ic, text, fn in (("image", "Screenshot", self._add_shot), ("paste", "Paste", self._paste),
                             ("map", "Mini-map", self._new_map),
                             ("note", "Note", lambda: self.board.add("note", data=dict(text=""))),
                             ("pin", "Places", lambda: self.board.add("coords"))):
            b = QPushButton("  " + text)
            b.setObjectName("AddBlock")
            icons.bind(b, ic, 15)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            tools.addWidget(b)
        right.addLayout(tools)
        right.addStretch(1)
        hv.addLayout(right)
        v.addWidget(self.head)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("BoardScroll")
        self.scroll.setWidgetResizable(False)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.board = Board(self)
        self.scroll.setWidget(self.board)
        self.scroll.verticalScrollBar().setSingleStep(24)
        v.addWidget(self.scroll, 1)

    def _cover_saved(self, sid):
        if self.s is not None and sid == self.s["id"]:
            self.head.set_art(_cover_image(self.s))

    def intro(self):
        _rise(self.head, 0, -16, 440, 40)
        _rise(self.scroll, 0, 22, 480, 120)

    def _new_map(self):
        """A mini-map: its place and structures first chosen in the editor."""
        ed = MapEditor(self.s, None, self.window())
        if ed.exec():
            self.board.add("map", data=ed.data())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.s is not None:
            self.board.grow()

    def load(self, s):
        self.s = s
        self.sg.setPixmap(brand.sigil(int(s["seed"]), 76))
        self.name.setText(s.get("name", ""))
        self.num.setText(s["seed"])
        self.num.setFont(theme.num_font(16, QFont.Weight.Bold, display=True))
        self.c_ver.setText("Java " + version_short(s) + ("  ·  Large biomes" if s.get("large") else ""))
        self._fill_tags()
        self._sync_fav()
        self.head.set_art(_cover_image(s))
        self.board.load(s)
        if not getattr(self, "_cover_hooked", False):
            self._cover_hooked = True
            renderer().cover_saved.connect(self._cover_saved)
        self.scroll.verticalScrollBar().setValue(0)
        self.scroll.horizontalScrollBar().setValue(0)

    def _sync_fav(self):
        on = bool(self.s.get("fav"))
        self.fav.setIcon(icons.icon("star_fill" if on else "star", 24, GOLD if on else theme.TEXT2,
                                    GOLD))
        self.fav.setToolTip("Favourite" if on else "Add to the favourites")

    def _toggle_fav(self):
        self.s["fav"] = not self.s.get("fav")
        store().touch(seed=self.s)
        self._sync_fav()
        if self.s["fav"] and motion.enabled():                  # a little pop
            def step(t):
                k = 1 + 0.35 * math.sin(math.pi * t)
                self.fav.setIconSize(QSize(int(24 * k), int(24 * k)))
            motion.run(self.fav, 260, step, lambda: self.fav.setIconSize(QSize(24, 24)))

    def _rename(self):
        if self.s is not None and self.name.text().strip() != self.s.get("name", ""):
            self.s["name"] = self.name.text().strip()
            store().touch(seed=self.s)

    def _fill_tags(self):
        while self.tags.count():
            it = self.tags.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for t in self.s.get("tags") or []:
            c = _TagChip(t)
            c.setToolTip("Remove this tag")
            c.clicked.connect(lambda _c=False, t=t: self._untag(t))
            self.tags.addWidget(c)
        add = QLineEdit()
        add.setObjectName("TagAdd")
        add.setPlaceholderText("+  Add a tag")
        add.setFixedWidth(128)
        known = sorted({t for x in store().seeds for t in (x.get("tags") or [])} - set(self.s.get("tags") or []),
                       key=str.lower)
        if known:                                    # the tags already used on other seeds
            from PySide6.QtWidgets import QCompleter
            cp = QCompleter(known, add)
            cp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            cp.setFilterMode(Qt.MatchFlag.MatchContains)
            add.setCompleter(cp)
        add.returnPressed.connect(lambda w=add: self._tag(w.text(), again=True))
        self.tags.addWidget(add)
        self.tag_edit = add

    def focus_tag(self):
        """The tag field, ready to type."""
        QTimer.singleShot(0, lambda: (self.tag_edit.setFocus(), self.tag_edit.selectAll()))

    def _tag(self, t, again=False):
        t = t.strip()
        if t and t not in self.s.setdefault("tags", []):
            self.s["tags"].append(t)
            store().touch(seed=self.s)
        self._fill_tags()
        if again and t:
            self.focus_tag()                         # Enter: the tag goes in, the field stays for another

    def _untag(self, t):
        if t in self.s.get("tags", []):
            self.s["tags"].remove(t)
            store().touch(seed=self.s)
        self._fill_tags()

    def _shelves_menu(self):
        m = QMenu(self)
        acts = {}
        for h in store().shelves:
            a = m.addAction(h["name"])
            a.setCheckable(True)
            a.setChecked(h["id"] in self.s.get("shelves", []))
            acts[a] = h["id"]
        if acts:
            m.addSeparator()
        a_new = m.addAction("New library…")
        got = m.exec(self.shelves_b.mapToGlobal(QPoint(0, self.shelves_b.height() + 4)))
        if got is a_new:
            name, ok = QInputDialog.getText(self, "New library", "Name:")
            if ok and name.strip():
                h = store().add_shelf(name.strip())
                self.s.setdefault("shelves", []).append(h["id"])
                store().touch(seed=self.s)
        elif got in acts:
            shid = acts[got]
            sh = self.s.setdefault("shelves", [])
            sh.remove(shid) if shid in sh else sh.append(shid)
            store().touch(seed=self.s)

    def _more(self):
        m = QMenu(self)
        a_map = m.addAction("Open the map")
        a_del = m.addAction("Delete this seed…")
        got = m.exec(self.mapToGlobal(QPoint(self.width() - 220, 70)))
        if got is a_map:
            self.window().use_seed(self.s, at=True)
        elif got is a_del:
            if QMessageBox.question(self, "Delete", f"Delete « {label(self.s)} » and its board?\n"
                                    "(its pictures go to seed_library/_trash)") == QMessageBox.StandardButton.Yes:
                store().remove_seed(self.s["id"])
                self.back.emit()

    def _add_shot(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Screenshots", user_path(),
                                                "Pictures (*.png *.jpg *.jpeg *.bmp *.webp *.gif)")
        for i, p in enumerate(paths):
            img = QImage(p)
            if not img.isNull():
                self.board.add_image(img, caption=os.path.splitext(os.path.basename(p))[0])

    def _paste(self):
        cb = QGuiApplication.clipboard()
        img = cb.image()
        if not img.isNull():
            self.board.add_image(img)
        elif cb.text().strip():
            self.board.add("note", data=dict(text=cb.text().strip()))
        else:
            QMessageBox.information(self, "Paste", "The clipboard holds no picture and no text.\n"
                                    "(Win + Shift + S takes a screenshot to paste.)")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_V and e.modifiers() & Qt.KeyboardModifier.ControlModifier \
                and not isinstance(QGuiApplication.focusObject(), (QLineEdit, QPlainTextEdit)):
            self._paste()
            return
        if e.key() == Qt.Key.Key_Escape:
            self.back.emit()
            return
        super().keyPressEvent(e)


# ── the mini-map editor (19/11 bis) ─────────────────────────────────────────
def _nearest_job(wkey, key, cx, cz):
    """For the renderer's thread: the structure `key` nearest to (cx, cz), or None."""
    def fn():
        import cubiomes_wrap as cw
        from .map_engine import _thread_lib
        lib = _thread_lib(wkey, 0)
        if key == "stronghold":
            pts = renderer().strongholds(lib, wkey)
        else:
            t = cw.ALL_STRUCTURE_TYPES[key]
            pts = []
            for rad in (1536, 6144, 24576):
                pts = lib.find_structures_in_box(t, cx - rad, cz - rad, cx + rad, cz + rad, max_count=48)
                if pts:
                    break
        if not pts:
            return None
        a, b = min(pts, key=lambda q: (q[0] - cx) ** 2 + (q[1] - cz) ** 2)
        return (int(a), int(b))
    return fn


class _MapCanvas(QWidget):
    """The editor's big map: drag to move, wheel to zoom (around the mouse), a click centres
    there (on an icon: on that structure). A cross marks the centre: the mini-map's place."""
    moved = Signal()

    def __init__(self, ed):
        super().__init__(ed)
        self.ed = ed
        self.img, self.info, self._view = None, {}, None
        self._tok = 0
        self._press = None
        self._hits = []
        self._hot = None
        self._mouse = None
        self._flash = 0.0
        self.setMouseTracking(True)
        self.setMinimumSize(520, 420)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(90)
        self._t.timeout.connect(self.request)
        renderer().done.connect(self._in)

    def bpp(self):
        return self.ed.d["bpp"]

    def request(self):
        d = self.ed.d
        self._tok += 1
        sc, w, h = view_cells(d["bpp"], self.width(), self.height())
        renderer().request(("edit", id(self)), self._tok, world_key(self.ed.s), d["x"], d["z"], sc, w, h,
                           structs=map_structs(d), around_spawn=self.ed.need_spawn, prio=0,
                           noise=map_noise(d, getattr(self.ed.parent(), "win", None)))

    def later(self):
        self.update()
        self._t.start()

    def _in(self, owner, tok, img, info):
        if owner == ("spawn", world_key(self.ed.s)):
            self.later()
            return
        if owner != ("edit", id(self)):
            return
        try:
            if tok != self._tok or img is None:
                return
            if self.ed.need_spawn and info.get("spawn"):       # a new mini-map: at the spawn
                self.ed.need_spawn = False
                self.ed.d["x"], self.ed.d["z"] = int(info["cx"]), int(info["cz"])
                self.ed._fill_xz()
            self.img, self.info = img, info
            self.update()
        except RuntimeError:
            pass

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.later()

    def to_world(self, pos):
        return (int(self.ed.d["x"] + (pos.x() - self.width() / 2) * self.bpp()),
                int(self.ed.d["z"] + (pos.y() - self.height() / 2) * self.bpp()))

    def flash(self):
        motion.run(self, 700, self._fl)

    def _fl(self, t):
        self._flash = 1.0 - t
        self.update()

    # mouse
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = (e.position(), self.ed.d["x"], self.ed.d["z"], False)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        self._mouse = e.position()
        if self._press is not None:
            p0, x0, z0, moved = self._press
            dd = e.position() - p0
            if moved or abs(dd.x()) + abs(dd.y()) > 4:
                self._press = (p0, x0, z0, True)
                self.ed.set_place(int(x0 - dd.x() * self.bpp()), int(z0 - dd.y() * self.bpp()), live=True)
            return
        hot = next((h for h in self._hits if h[0].contains(e.position())), None)
        if hot != self._hot:
            self._hot = hot
            self.setCursor(Qt.CursorShape.PointingHandCursor if hot else Qt.CursorShape.OpenHandCursor)
        self.update()

    def leaveEvent(self, e):
        self._mouse = None
        self._hot = None
        self.update()

    def mouseReleaseEvent(self, e):
        if self._press is None:
            return
        _p0, _x0, _z0, moved = self._press
        self._press = None
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._hot else Qt.CursorShape.OpenHandCursor)
        if not moved:                                      # a click: there becomes the centre
            if self._hot:
                self.ed.set_place(self._hot[2], self._hot[3])
            else:
                self.ed.set_place(*self.to_world(e.position()))
            self.flash()
        self.ed.place_done()

    def wheelEvent(self, e):
        d = self.ed.d
        nb = zoom_step(d["bpp"], -1 if e.angleDelta().y() > 0 else 1)
        if nb != d["bpp"]:
            wx, wz = self.to_world(e.position())               # the point under the mouse stays
            nx = int(wx - (e.position().x() - self.width() / 2) * nb)
            nz = int(wz - (e.position().y() - self.height() / 2) * nb)
            self.ed.set_zoom(nb, nx, nz)
        e.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        d = self.ed.d
        # the last picture, wherever it was made: it follows the moves and zooms until the next
        self._hits = paint_map(p, r, self.img, self.info, d["x"], d["z"], d["bpp"], d["spawn"], 16, icon=20,
                               hot=self._hot, show_origin=d["origin"])
        # the centre: the mini-map's place
        c = r.center()
        p.setPen(QPen(QColor(0, 0, 0, 120), 4))
        p.drawEllipse(c, 9, 9)
        p.setPen(QPen(QColor(GOLD), 2))
        p.drawEllipse(c, 9, 9)
        p.drawLine(QPointF(c.x() - 16, c.y()), QPointF(c.x() - 11, c.y()))
        p.drawLine(QPointF(c.x() + 11, c.y()), QPointF(c.x() + 16, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 16), QPointF(c.x(), c.y() - 11))
        p.drawLine(QPointF(c.x(), c.y() + 11), QPointF(c.x(), c.y() + 16))
        if self._flash > 0:                                # a ring that widens: "here"
            q = QColor(GOLD)
            q.setAlphaF(self._flash * 0.8)
            p.setPen(QPen(q, 2))
            rad = 10 + (1 - self._flash) * 40
            p.drawEllipse(c, rad, rad)
        # the frame the board's mini-map will show
        bw, bh = self.ed.block_size                        # (same zoom: same pixels)
        fr = QRectF(c.x() - bw / 2, c.y() - bh / 2, bw, bh)
        p.setPen(QPen(QColor(255, 255, 255, 110), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(fr, 12, 12)
        # labels
        if self._hot:
            from . import struct_icons
            rr = self._hot[0]
            txt = ("Spawn" if self._hot[1] == "spawn" else struct_icons.label(self._hot[1])) + \
                f"  ·  {self._hot[2]}, {self._hot[3]}"
            map_chip(p, QPointF(rr.center().x() + 14, rr.top() - 20), txt)
        map_chip(p, QPointF(r.left() + 10, r.bottom() - 28), f"{d['x']}, {d['z']}  ·  {zoom_label(d['bpp'])}")
        if self._mouse is not None and not self._hot:
            wx, wz = self.to_world(self._mouse)
            t = f"{wx}, {wz}"
            p.setFont(theme.num_font(7.8))
            tw = p.fontMetrics().horizontalAdvance(t) + 16
            map_chip(p, QPointF(r.right() - tw - 10, r.bottom() - 28), t)
        p.setPen(QPen(QColor(theme.LINE2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)
        p.end()


class _Section(QLabel):
    def __init__(self, text):
        super().__init__(text.upper())
        self.setObjectName("MapEdSection")


class MapEditor(QDialog):
    """A mini-map's place, zoom and structures, chosen on a big live map (19/11 bis)."""

    def __init__(self, s, data=None, parent=None):
        super().__init__(parent)
        self.s = s
        self.new = data is None
        data = dict(data or {})
        mc = world_key(s)[1]
        self.choices = [k for k in MAP_CHOICES if struct_ok(k, mc)]
        sp = renderer().spawn_of(world_key(s))
        x, z = data.get("x"), data.get("z")
        self.need_spawn = x is None and sp is None          # (known once the first picture comes)
        if x is None:
            x, z = sp if sp else (0, 0)
        self.d = dict(x=int(x), z=int(z), bpp=map_bpp(data),
                      octave=list(data["octave"]) if data.get("octave") else None,
                      structs=[k for k in (data.get("structs") or MAP_STRUCTS) if k in self.choices],
                      spawn=bool(data.get("spawn", True)), frozen=bool(data.get("frozen", False)),
                      origin=bool(data.get("origin", False)),
                      noise=data.get("noise") if noise_ok(data.get("noise"), mc) else None,
                      npal=data.get("npal") or default_noise_palette(getattr(parent, "win", None)),
                      nalpha=float(data.get("nalpha", 1.0)))
        self.noises = [n for n in MAP_NOISES if noise_ok(n, mc)]
        self.block_size = BLOCK_SIZES["map"]
        if not self.new:
            for b in s.get("board", []):
                if b.get("data") is data or (b["kind"] == "map" and b["data"] == data):
                    self.block_size = (b["w"], b["h"])
                    break
        self.setObjectName("MapEditor")
        self.setWindowTitle("Mini-map")
        self.resize(1100, 720)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 16, 16, 16)
        h.setSpacing(16)
        self.canvas = _MapCanvas(self)
        h.addWidget(self.canvas, 1)

        side = QFrame()
        side.setObjectName("MapEdSide")
        side.setFixedWidth(330)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(16, 16, 16, 16)
        sv.setSpacing(8)
        title = QLabel("Mini-map")
        title.setObjectName("SetPageTitle")
        sv.addWidget(title)

        from PySide6.QtWidgets import QComboBox
        sv.addWidget(_Section("Place"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.ex, self.ez = QLineEdit(), QLineEdit()
        for lab, e in (("X", self.ex), ("Z", self.ez)):
            lb = QLabel(lab)
            lb.setObjectName("Muted")
            row.addWidget(lb)
            e.setObjectName("MapEdNum")
            e.setFont(theme.num_font(9.5))
            e.editingFinished.connect(self._typed)
            row.addWidget(e, 1)
        sv.addLayout(row)
        places = QWidget()
        pf = FlowLayout(places, 6, 6)
        for text, fn in self._places():
            b = QPushButton(text)
            b.setObjectName("MapPlace")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _c=False, fn=fn: self._go_to(fn()))
            pf.addWidget(b)
        sv.addWidget(places)
        fr = QHBoxLayout()
        fr.setSpacing(6)
        from . import struct_icons
        self.find = QComboBox()
        for k in self.choices:
            self.find.addItem(struct_icons.icon(k, 16) if hasattr(struct_icons, "icon") else
                              icons.icon("target", 16), struct_icons.label(k), k)
        fr.addWidget(self.find, 1)
        self.findb = QPushButton("  Nearest")
        self.findb.setProperty("kind", "ghost")
        icons.bind(self.findb, "target", 15)
        self.findb.clicked.connect(self._nearest)
        fr.addWidget(self.findb)
        sv.addLayout(fr)
        self.found = QLabel("")
        self.found.setObjectName("Muted")
        self.found.hide()                                  # (shown with a search's answer)
        sv.addWidget(self.found)

        sv.addSpacing(4)
        sv.addWidget(_Section("Zoom"))
        from .widgets import EasySlider, Segmented
        zr = QHBoxLayout()
        zr.setSpacing(6)
        zo = QToolButton()
        icons.bind(zo, "minus", 15)
        zo.setAutoRepeat(True)
        zo.clicked.connect(lambda: self.set_zoom(zoom_step(self.d["bpp"], 1)))
        zr.addWidget(zo)
        self.zoom = EasySlider()                        # (20/11) from 4 px a block to 256 blocks a px
        self.zoom.setRange(0, len(ZOOMS) - 1)
        self.zoom.setInvertedAppearance(True)           # right: closer
        self.zoom.setValue(ZOOMS.index(zoom_step(self.d["bpp"], 0)))
        self.zoom.valueChanged.connect(lambda i: self.set_zoom(ZOOMS[i], from_slider=True))
        zr.addWidget(self.zoom, 1)
        zi = QToolButton()
        icons.bind(zi, "plus", 15)
        zi.setAutoRepeat(True)
        zi.clicked.connect(lambda: self.set_zoom(zoom_step(self.d["bpp"], -1)))
        zr.addWidget(zi)
        self.zoom_v = QLabel(zoom_label(self.d["bpp"]))
        self.zoom_v.setObjectName("MapEdZoom")
        self.zoom_v.setFont(theme.num_font(9))
        self.zoom_v.setFixedWidth(46)
        self.zoom_v.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        zr.addWidget(self.zoom_v)
        sv.addLayout(zr)

        # (19/11 ter) colours: the biomes or a noise map, its palette, how much of it
        sv.addSpacing(4)
        sv.addWidget(_Section("Colours"))
        self.layer = QComboBox()
        self.layer.addItem("Biomes", None)
        for n in self.noises:
            self.layer.addItem(n, n)
        self.layer.setCurrentIndex(max(0, self.layer.findData(self.d["noise"])))
        self.layer.currentIndexChanged.connect(self._layer)
        sv.addWidget(self.layer)
        self.nbox = QWidget()
        nb = QVBoxLayout(self.nbox)
        nb.setContentsMargins(0, 0, 0, 0)
        nb.setSpacing(6)
        from .palette_combo import fill_palette_combo
        self.npal = QComboBox()
        fill_palette_combo(self.npal, self.d["npal"])
        self.npal.currentIndexChanged.connect(self._npal)
        nb.addWidget(self.npal)
        orow = QHBoxLayout()
        orow.setSpacing(8)
        ol = QLabel("Opacity")
        ol.setObjectName("Muted")
        orow.addWidget(ol)
        from .widgets import EasySlider
        self.alpha = EasySlider()
        self.alpha.setRange(10, 100)
        self.alpha.setValue(int(round(self.d["nalpha"] * 100)))
        self.alpha.valueChanged.connect(self._alpha)
        orow.addWidget(self.alpha, 1)
        self.alpha_v = QLabel(f"{self.alpha.value()} %")
        self.alpha_v.setObjectName("Muted")
        self.alpha_v.setFixedWidth(38)
        orow.addWidget(self.alpha_v)
        nb.addLayout(orow)
        # (20/11) octaves of a climate noise (as the map: Alt+1…9, Alt+Shift+1…9, Alt+0, Alt+O)
        self.obox = QWidget()
        ob = QHBoxLayout(self.obox)
        ob.setContentsMargins(0, 0, 0, 0)
        ob.setSpacing(6)
        self.oct = QComboBox()
        self.oct.currentIndexChanged.connect(self._oct)
        ob.addWidget(self.oct, 1)
        self.oct_mode = Segmented(["Only", "Up to"], "Only")
        self.oct_mode.changed.connect(lambda *_: self._oct())
        ob.addWidget(self.oct_mode)
        nb.addWidget(self.obox)
        sv.addWidget(self.nbox)
        self.nbox.setVisible(bool(self.d["noise"]))
        if not self.noises:
            self.layer.setEnabled(False)

        sv.addSpacing(4)
        hr = QHBoxLayout()
        hr.addWidget(_Section("On the map"))
        hr.addStretch(1)
        for text, fn in (("All", lambda: self._all(True)), ("None", lambda: self._all(False))):
            b = QPushButton(text)
            b.setObjectName("CardLink")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(fn)
            hr.addWidget(b)
        sv.addLayout(hr)
        chips = QWidget()
        cf = FlowLayout(chips, 6, 6)
        self.chips = {}
        for k in ["spawn", "origin"] + self.choices:
            b = QPushButton(" " + {"spawn": "Spawn", "origin": "Origin"}.get(k) if k in ("spawn", "origin")
                            else " " + struct_icons.label(k))
            b.setObjectName("MapChip")
            b.setCheckable(True)
            b.setChecked(self.d[k] if k in ("spawn", "origin") else k in self.d["structs"])
            b.setIcon(QIcon(struct_icons.pixmap(k, 16)))
            b.setIconSize(QSize(16, 16))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.toggled.connect(lambda on, k=k: self._chip(k, on))
            cf.addWidget(b)
            self.chips[k] = b
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(chips)
        sa.setObjectName("MapEdChips")
        sv.addWidget(sa, 1)
        self.far = QLabel(f"Structures show up to {zoom_label(STRUCT_MAX_BPP)} (strongholds: always).")
        self.far.setObjectName("Muted")
        sv.addWidget(self.far)

        lr = QHBoxLayout()
        from .settings_dialog import Switch
        self.live = Switch(not self.d["frozen"])
        self.live.toggled.connect(lambda on: self.d.__setitem__("frozen", not on))
        lr.addWidget(self.live)
        ll = QLabel("Drag and zoom it on the board")
        ll.setObjectName("Muted")
        lr.addWidget(ll, 1)
        sv.addLayout(lr)

        br = QHBoxLayout()
        br.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("kind", "ghost")
        cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        ok = QPushButton("  Add to the board" if self.new else "  Save")
        ok.setProperty("kind", "primary")
        icons.bind(ok, "plus" if self.new else "check", 15, "on_accent", "on_accent")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        br.addWidget(ok)
        sv.addLayout(br)
        h.addWidget(side)
        renderer().done.connect(self._done)
        self._fill_xz()
        self._sync_far()
        self._sync_noise_ui()
        self.toast = Toast(self)
        install_noise_keys(self, self._key)
        self.canvas.request()

    # the places offered
    def _places(self):
        out = [("Spawn", lambda: renderer().spawn_of(world_key(self.s))), ("0, 0", lambda: (0, 0))]
        win = getattr(self.parent(), "win", None) if self.parent() is not None else None
        mp = getattr(win, "map", None)
        if mp is not None and hasattr(mp, "cx"):
            out.append(("Main map view", lambda: (int(mp.cx), int(mp.cz))))
        for b in self.s.get("board", []):
            if b["kind"] != "coords":
                continue
            for pt in b["data"].get("points", []):
                try:
                    px, pz = int(float(pt.get("x"))), int(float(pt.get("z")))
                except (TypeError, ValueError):
                    continue
                out.append((pt.get("name") or f"{px}, {pz}", lambda px=px, pz=pz: (px, pz)))
        return out

    def data(self):
        return dict(self.d)

    def _fill_xz(self):
        for e, v in ((self.ex, self.d["x"]), (self.ez, self.d["z"])):
            if not (e.hasFocus() and e.isModified()):          # (not what is being typed)
                e.setText(str(v))
                e.setModified(False)

    def _typed(self):
        try:
            self._go(int(float(self.ex.text().replace(" ", ""))), int(float(self.ez.text().replace(" ", ""))))
        except ValueError:
            self._fill_xz()

    def _go_to(self, pt):
        if pt is None:                                     # the spawn, not known yet: found first
            self.need_spawn = True
            self.canvas.request()
            return
        self._go(*pt)

    def _go(self, x, z):
        self.set_place(x, z)
        self.canvas.flash()
        self.place_done()

    def set_place(self, x, z, live=False):
        self.d["x"], self.d["z"] = int(x), int(z)
        self._fill_xz()
        self.canvas.later()

    def place_done(self):
        self.canvas.request()

    def set_zoom(self, bpp, x=None, z=None, from_slider=False):
        bpp = float(bpp)
        if bpp == self.d["bpp"] and x is None:
            return
        self.d["bpp"] = bpp
        if x is not None:
            self.d["x"], self.d["z"] = int(x), int(z)
            self._fill_xz()
        if not from_slider:
            self.zoom.blockSignals(True)
            self.zoom.setValue(ZOOMS.index(zoom_step(bpp, 0)))
            self.zoom.blockSignals(False)
        self.zoom_v.setText(zoom_label(bpp))
        self._sync_far()
        self.canvas.later()

    def _sync_far(self):
        self.far.setVisible(self.d["bpp"] > STRUCT_MAX_BPP)

    def _layer(self, *_):
        n = self.layer.currentData()
        if n != self.d["noise"]:
            self.d["octave"] = None
        self.d["noise"] = n
        self._sync_noise_ui()
        self.canvas.later()

    def _sync_noise_ui(self):
        """The colour controls as the data says (after a key or a choice)."""
        from .map_engine import CLIMATE
        n = self.d["noise"]
        self.layer.blockSignals(True)
        self.layer.setCurrentIndex(max(0, self.layer.findData(n)))
        self.layer.blockSignals(False)
        self.nbox.setVisible(bool(n))
        rows = octave_rows(world_key(self.s), n) if n in CLIMATE else []
        self.obox.setVisible(bool(rows))
        self.oct.blockSignals(True)
        self.oct.clear()
        self.oct.addItem("All octaves", None)
        for pair, inclb, lab in rows:
            self.oct.addItem(lab, f"{pair},{int(bool(inclb))}")
        o = self.d.get("octave")
        i = self.oct.findData(f"{o[0]},{int(bool(o[1]))}") if o else 0
        self.oct.setCurrentIndex(max(0, i))
        self.oct.blockSignals(False)
        self.oct_mode.set_value("Only" if (not o or o[2]) else "Up to")
        self.oct_mode.setVisible(bool(o))

    def _oct(self, *_):
        v = self.oct.currentData()
        if v:
            pair, inclb = v.split(",")
            v = (int(pair), inclb == "1")
        self.d["octave"] = [v[0], v[1], self.oct_mode.value() == "Only"] if v else None
        self.oct_mode.setVisible(bool(v))
        self.canvas.later()

    def _key(self, kind, *a):
        """The map's noise shortcuts, here."""
        self.toast.say(apply_noise_key(self.d, world_key(self.s), kind, *a))
        self._sync_noise_ui()
        self.canvas.later()

    def _npal(self, *_):
        name = self.npal.currentData()
        if name:
            self.d["npal"] = name
            self.canvas.later()

    def _alpha(self, v):
        self.d["nalpha"] = v / 100.0
        self.alpha_v.setText(f"{v} %")
        self.canvas.later()

    def _chip(self, k, on):
        if k in ("spawn", "origin"):
            self.d[k] = on
            self.canvas.update()
            return
        if on and k not in self.d["structs"]:
            self.d["structs"].append(k)
        elif not on and k in self.d["structs"]:
            self.d["structs"].remove(k)
        self.canvas.later()

    def _all(self, on):
        for b in self.chips.values():
            b.setChecked(on)

    def _nearest(self):
        k = self.find.currentData()
        if not k:
            return
        self.findb.setEnabled(False)
        self.found.setText("Looking…")
        self.found.show()
        self._ask = k
        if k not in self.d["structs"]:
            self.chips[k].setChecked(True)                 # what you look for is shown
        renderer().call(("near", id(self)), k, _nearest_job(world_key(self.s), k, self.d["x"], self.d["z"]))

    def _done(self, owner, tok, img, info):
        if owner != ("near", id(self)):
            return
        try:
            self.findb.setEnabled(True)
            got = info.get("call")
            if not got:
                self.found.setText("None near here.")
                return
            from . import struct_icons
            dist = int(math.hypot(got[0] - self.d["x"], got[1] - self.d["z"]))
            self.found.setText(f"{struct_icons.label(tok)}  ·  {got[0]}, {got[1]}  ·  {dist} blocks away")
            if self.d["bpp"] > STRUCT_MAX_BPP:
                self.set_zoom(8.0)
            self._go(*got)
        except RuntimeError:
            pass

    def showEvent(self, e):
        super().showEvent(e)
        if motion.enabled():
            self.setWindowOpacity(0.0)
            motion.run(self, 200, lambda t: self.setWindowOpacity(t))


# ── adding a seed by hand (20/11) ───────────────────────────────────────────
class _Painted(QWidget):
    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def paintEvent(self, e):
        self._fn(e)


class AddTile(QWidget):
    """The last tile of a grid: « Add a seed » (dashed, it lights up under the mouse)."""
    clicked = Signal()

    def __init__(self, w, h, text="Add a seed", parent=None):
        super().__init__(parent)
        self.setFixedSize(w, h)
        self.text = text
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hov, self._an = 0.0, None

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
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = self._hov
        r = QRectF(self.rect()).adjusted(1.5, 1.5 + 2 - 2 * k, -1.5, -3.5)
        g = QColor(GOLD)
        g.setAlphaF(0.04 + 0.08 * k)
        p.setBrush(g)
        line = motion.mix(theme.LINE2, GOLD, 0.25 + 0.6 * k)
        p.setPen(QPen(line, 1.4, Qt.PenStyle.DashLine))
        p.drawRoundedRect(r, 14, 14)
        c = r.center()
        rad = 20 + 2 * k
        disc = QColor(GOLD)
        disc.setAlphaF(0.14 + 0.16 * k)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(disc)
        p.drawEllipse(QPointF(c.x(), c.y() - 12), rad, rad)
        pm = icons.pixmap("plus", 22, GOLD)
        p.drawPixmap(QRectF(c.x() - 11, c.y() - 23, 22, 22), pm, QRectF(pm.rect()))
        p.setPen(motion.mix(theme.TEXT2, GOLD, 0.6 * k))
        p.setFont(theme.ui_font(9.2, QFont.Weight.DemiBold))
        p.drawText(QRectF(r.left(), c.y() + 14, r.width(), 22), Qt.AlignmentFlag.AlignCenter, self.text)


class AddSeedDialog(QDialog):
    """(20/11) A seed typed in (or pasted, or drawn at random), its version and world, a name,
    tags, favourite, library — with its world drawn live as it is typed."""

    def __init__(self, parent=None, shelf=None, win=None):
        super().__init__(parent)
        from .settings_dialog import Switch
        from .world import DEFAULT_VERSION, VERSION_NAMES
        from PySide6.QtWidgets import QComboBox
        self.win = win
        self.setObjectName("AddSeed")
        self.setWindowTitle("Add a seed")
        self.resize(820, 470)
        self.result_id = None
        self._tok = 0
        self.img, self.info = None, {}
        h = QHBoxLayout(self)
        h.setContentsMargins(18, 18, 18, 18)
        h.setSpacing(18)
        # left: the world, live
        self.view = _Painted(self._paint_view)
        self.view.setMinimumSize(340, 380)
        h.addWidget(self.view, 1)
        # right: the form
        f = QVBoxLayout()
        f.setSpacing(8)
        t = QLabel("Add a seed")
        t.setObjectName("SetPageTitle")
        f.addWidget(t)
        f.addWidget(_Section("Seed"))
        sr = QHBoxLayout()
        sr.setSpacing(6)
        self.seed = QLineEdit()
        self.seed.setObjectName("AddSeedNum")
        self.seed.setPlaceholderText("A number, or a text")
        self.seed.setFont(theme.num_font(11))
        self.seed.textChanged.connect(self._changed)
        sr.addWidget(self.seed, 1)
        for ic, tip, fn in (("paste", "Paste", self._paste), ("dice", "A random seed", self._random)):
            b = QToolButton()
            icons.bind(b, ic, 17)
            b.setToolTip(tip)
            b.setFixedSize(34, 34)
            b.clicked.connect(fn)
            sr.addWidget(b)
        f.addLayout(sr)
        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        f.addWidget(self.hint)
        f.addWidget(_Section("World"))
        wr = QHBoxLayout()
        wr.setSpacing(10)
        self.ver = QComboBox()
        names = list(reversed(VERSION_NAMES))                # the newest first
        for n in names:
            self.ver.addItem(n, n)
        cur = None
        try:
            cur = win.world.version_name
        except Exception:                                     # noqa: BLE001
            pass
        self.ver.setCurrentIndex(max(0, self.ver.findData(cur or DEFAULT_VERSION)))
        self.ver.currentIndexChanged.connect(self._changed)
        wr.addWidget(self.ver, 1)
        self.large = Switch(False)
        self.large.toggled.connect(self._changed)
        wr.addWidget(self.large)
        ll = QLabel("Large biomes")
        ll.setObjectName("Muted")
        wr.addWidget(ll)
        f.addLayout(wr)
        f.addWidget(_Section("About it"))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name  (optional)")
        f.addWidget(self.name)
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("Tags, separated by commas  (optional)")
        f.addWidget(self.tags)
        fr = QHBoxLayout()
        fr.setSpacing(10)
        self.fav = Switch(True)
        fr.addWidget(self.fav)
        fl = QLabel("Favourite")
        fl.setObjectName("Muted")
        fr.addWidget(fl)
        fr.addSpacing(10)
        self.shelf = QComboBox()
        self.shelf.addItem("No library", None)
        for sh in store().shelves:
            self.shelf.addItem(sh["name"], sh["id"])
        if shelf:
            self.shelf.setCurrentIndex(max(0, self.shelf.findData(shelf)))
        fr.addWidget(self.shelf, 1)
        f.addLayout(fr)
        f.addStretch(1)
        self.dup = QPushButton("")
        self.dup.setObjectName("CardLink")
        self.dup.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dup.clicked.connect(self._open_dup)
        self.dup.hide()
        f.addWidget(self.dup, 0, Qt.AlignmentFlag.AlignLeft)
        br = QHBoxLayout()
        br.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.setProperty("kind", "ghost")
        cancel.clicked.connect(self.reject)
        br.addWidget(cancel)
        self.ok = QPushButton("  Add to the library")
        self.ok.setProperty("kind", "primary")
        icons.bind(self.ok, "star_fill", 15, "on_accent", "on_accent")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._add)
        br.addWidget(self.ok)
        f.addLayout(br)
        side = QFrame()
        side.setObjectName("MapEdSide")
        side.setFixedWidth(380)
        side.setLayout(f)
        f.setContentsMargins(18, 18, 18, 18)
        h.addWidget(side)
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.setInterval(280)
        self._t.timeout.connect(self._request)
        renderer().done.connect(self._in)
        self._changed()
        self.seed.setFocus()

    # the seed as the game reads it
    def value(self):
        from .world import parse_seed
        return parse_seed(self.seed.text())

    def _paste(self):
        t = QGuiApplication.clipboard().text().strip().split()
        if t:
            self.seed.setText(t[0])

    def _random(self):
        from .world import random_seed
        self.seed.setText(str(random_seed()))

    def _changed(self, *_):
        v = self.value()
        txt = self.seed.text().strip()
        if v is None:
            self.hint.setText("")
        elif str(v) != txt:
            self.hint.setText(f"A text seed: the game makes it  {v}")
        else:
            self.hint.setText("")
        self.hint.setVisible(bool(self.hint.text()))
        dup = store().find(v, self.ver.currentData(), self.large.isChecked()) if v is not None else None
        self._dup = dup
        if dup is not None:
            self.dup.setText(f"  Already in the library: « {label(dup)} » — open it")
            icons.bind(self.dup, "seed_library", 15)
        self.dup.setVisible(dup is not None)
        self.ok.setEnabled(v is not None and dup is None)
        self.img = None if v is None else self.img
        self.view.update()
        if v is not None:
            self._t.start()

    def _key(self):
        v = self.value()
        if v is None:
            return None
        import cubiomes_wrap as cw
        mc = cw.MC_VERSIONS.get(self.ver.currentData())
        return None if mc is None else (int(v), mc, bool(self.large.isChecked()))

    def _request(self):
        k = self._key()
        if k is None:
            return
        self._tok += 1
        w, h = self.view.width(), self.view.height()
        sc, cw_, ch_ = view_cells(8.0, w, h)
        renderer().request(("add", id(self)), (self._tok, k), k, 0, 0, sc, cw_, ch_,
                           structs=["village", "stronghold", "monument", "mansion"], around_spawn=True, prio=0)

    def _in(self, owner, tok, img, info):
        if owner != ("add", id(self)):
            return
        try:
            if tok[0] != self._tok or img is None:
                return
            self.img, self.info = img, info
            self.view.update()
        except RuntimeError:
            pass

    def _paint_view(self, e):
        p = QPainter(self.view)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.view.rect())
        if self.img is not None and self.info.get("cx") is not None:
            paint_map(p, r, self.img, self.info, self.info["cx"], self.info["cz"], 8.0, True, 16, icon=18)
            v = self.value()
            if v is not None:                          # its sigil, the spawn's coordinates
                sg = brand.sigil(int(v), 40)
                p.drawPixmap(QRectF(r.left() + 14, r.top() + 14, 40, 40), sg, QRectF(sg.rect()))
            sp = self.info.get("spawn")
            if sp:
                map_chip(p, QPointF(r.left() + 12, r.bottom() - 30), f"Spawn  {sp[0]}, {sp[1]}  ·  1:8")
        else:
            path = QPainterPath()
            path.addRoundedRect(r, 16, 16)
            g = QLinearGradient(r.topLeft(), r.bottomRight())
            a, b = QColor(GOLD), QColor(theme.BG3)
            a.setAlphaF(0.10)
            g.setColorAt(0, a)
            g.setColorAt(1, b)
            p.fillPath(path, g)
            pm = icons.pixmap("seed_library", 64, GOLD)
            c = r.center()
            p.setOpacity(0.55)
            p.drawPixmap(QRectF(c.x() - 32, c.y() - 50, 64, 64), pm, QRectF(pm.rect()))
            p.setOpacity(1.0)
            p.setPen(QColor(theme.TEXT3))
            p.drawText(QRectF(r.left(), c.y() + 26, r.width(), 24), Qt.AlignmentFlag.AlignCenter,
                       "Its world shows here" if self.value() is None else "Drawing its world…")
        p.setPen(QPen(QColor(theme.LINE2), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), 16, 16)
        p.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.value() is not None:
            self._t.start()

    def _open_dup(self):
        if self._dup is not None:
            self.result_id = self._dup["id"]
            self.done(2)                                  # (2: open that one)

    def _add(self):
        v = self.value()
        if v is None or self._dup is not None:
            return
        s = store().add_seed(v, self.ver.currentData(), self.large.isChecked(), self.name.text().strip(),
                             shelf=self.shelf.currentData())
        s["tags"] = [t.strip() for t in self.tags.text().split(",") if t.strip()]
        s["fav"] = bool(self.fav.isChecked())
        if self.img is not None:                          # its world as a first cover
            try:
                self.img.save(thumb_path(s["id"]))
            except Exception:                             # noqa: BLE001
                pass
        store().touch(seed=s)
        self.result_id = s["id"]
        self.accept()

    def showEvent(self, e):
        super().showEvent(e)
        if motion.enabled():
            self.setWindowOpacity(0.0)
            motion.run(self, 200, lambda t: self.setWindowOpacity(t))


class ImageViewer(QDialog):
    """A screenshot, as large as the screen allows; a click closes it."""

    def __init__(self, img, caption, parent=None):
        super().__init__(parent)
        self.setWindowTitle(caption or "Screenshot")
        self.img = img
        scr = QGuiApplication.primaryScreen().availableGeometry()
        k = min(scr.width() * 0.86 / img.width(), scr.height() * 0.86 / img.height(), 1.0)
        self.resize(int(img.width() * k), int(img.height() * k))

    def mousePressEvent(self, e):
        self.accept()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#000000"))
        k = min(self.width() / self.img.width(), self.height() / self.img.height())
        w, h = self.img.width() * k, self.img.height() * k
        p.drawImage(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.img)


# ── the window ───────────────────────────────────────────────────────────────
# ── the window's sky (19/11 bis): a soft mesh of coloured glows over the theme's background,
#    a new one each time the library opens; computed in float and dithered (no banding) ─────────
SKIES = (
    ("Aurora", ("#3dffb0", "#35c7ff", "#9b6bff")),
    ("Sunset", ("#ff8a3a", "#e5689a", "#7a5cff")),
    ("Ocean", ("#2b7fff", "#22c3d6", "#3a4fd0")),
    ("Nether", ("#e5484d", "#ff8a3a", "#9a2a4a")),
    ("End", ("#b48cff", "#6a4ad0", "#e8e0a0")),
    ("Jungle", ("#3fb47a", "#b8e04a", "#2a9a8a")),
    ("Cherry", ("#ff9ccf", "#e5689a", "#ffc2a0")),
    ("Deep dark", ("#0fb3b3", "#2a6bb0", "#4ad0ff")),
    ("Gold", ("#f5b942", "#ff8a3a", "#a07aff")),
    ("Mushroom", ("#a07aff", "#e5689a", "#d0a0ff")),
    ("Ice", ("#9fe0ff", "#5aa8ff", "#c8b8ff")),
    ("Desert", ("#f5d76e", "#d4834a", "#e6b98a")),
)
_LAST_SKY = [-1]
_DITHER = []


def _hex3(h):
    c = QColor(h)
    return (c.red(), c.green(), c.blue())


def sky_image(w, h, idx, seed, base_hex, light=False):
    """The sky as a w×h QImage (RGB888): the base colour, a few large soft glows of the sky's
    colours (separable gaussians, gently warped by low noise), a light vignette, then a
    triangular dither so the 8-bit result shows no steps."""
    import numpy as np
    W, H = max(8, int(w)), max(8, int(h))
    w, h = max(8, W // 4), max(8, H // 4)          # the glows are smooth: a quarter, in float, then stretched
    rng = np.random.default_rng(seed)
    base = np.array(_hex3(base_hex), np.float32)
    cols = [np.array(_hex3(c), np.float32) for c in SKIES[idx % len(SKIES)][1]]
    img = np.empty((h, w, 3), np.float32)
    img[:] = base
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)
    y = np.linspace(0.0, 1.0, h, dtype=np.float32)
    asp = w / float(h)
    k = 0.10 if light else 0.19
    # five glows: corners and edges preferred, one may cross the middle
    spots = [(rng.uniform(-0.15, 0.35), rng.uniform(-0.2, 0.3)), (rng.uniform(0.65, 1.15), rng.uniform(0.7, 1.2)),
             (rng.uniform(0.6, 1.1), rng.uniform(-0.2, 0.35)), (rng.uniform(-0.1, 0.4), rng.uniform(0.65, 1.15)),
             (rng.uniform(0.3, 0.7), rng.uniform(0.25, 0.75))]
    order = rng.permutation(len(spots))
    try:
        from .backdrops import _value_noise
        warp = _value_noise(h, w, max(4.0, h / 1.6), rng)   # low noise: it only bends the glows
    except Exception:                                # noqa: BLE001
        warp = None
    for n, i in enumerate(order):
        cx, cy = spots[i]
        rx = rng.uniform(0.26, 0.52)
        ry = rx * asp * rng.uniform(0.7, 1.3)
        a = (k * rng.uniform(0.65, 1.15)) if n < 4 else k * 0.45
        gx = np.exp(-((x - cx) / rx) ** 2)
        gy = np.exp(-((y - cy) / ry) ** 2)
        wgt = np.outer(gy, gx) * a
        if warp is not None:
            wgt *= 0.65 + 0.7 * warp
        c = cols[n % len(cols)]
        img += wgt[:, :, None] * (c - img)
    # a soft vignette for depth (lighter themes: barely)
    vx = (x - 0.5) * 2.0
    vy = (y - 0.5) * 2.0
    v = 1.0 - (0.10 if not light else 0.03) * np.clip(np.add.outer(vy * vy, vx * vx) * 0.5, 0, 1)
    img *= v[:, :, None]
    # stretched to the full size (bilinear, still in float: no steps)
    fy = np.linspace(0, h - 1, H, dtype=np.float32)
    y0 = np.minimum(fy.astype(np.int32), h - 2)
    ty = (fy - y0)[:, None, None]
    img = img[y0] * (1 - ty) + img[y0 + 1] * ty
    fx = np.linspace(0, w - 1, W, dtype=np.float32)
    x0 = np.minimum(fx.astype(np.int32), w - 2)
    tx = (fx - x0)[None, :, None]
    img = img[:, x0] * (1 - tx) + img[:, x0 + 1] * tx
    h, w = H, W
    if not _DITHER:                                  # triangular noise, one tile made once
        r2 = np.random.default_rng(7)
        _DITHER.append((r2.random((128, 128, 3), dtype=np.float32) + r2.random((128, 128, 3), dtype=np.float32)
                        - 1.0))
    t = _DITHER[0]
    img += np.tile(t, (h // 128 + 1, w // 128 + 1, 1))[:h, :w]
    out = np.ascontiguousarray(np.clip(img + 0.5, 0, 255).astype(np.uint8))
    q = QImage(out.data, w, h, w * 3, QImage.Format.Format_RGB888)
    return q.copy()


def pick_sky():
    """A sky other than the last one."""
    import random
    n = len(SKIES)
    i = random.randrange(n - 1) if n > 1 else 0
    if _LAST_SKY[0] >= 0 and i >= _LAST_SKY[0]:
        i += 1
    _LAST_SKY[0] = i
    return i, random.randrange(1 << 30)


class SkyBackdrop(QObject):
    """(20/11) The Seed Library's sky behind another window (the session and drawing
    libraries): a new one at each opening, blooming out of the plain background; made
    again 140 ms after a resize (the last one stretched meanwhile) and on a theme change."""

    def __init__(self, w):
        super().__init__(w)
        self.w = w
        self.img = self.old = None
        self.t = 1.0
        self.idx, self.seed = pick_sky()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(140)
        self._timer.timeout.connect(self._make)
        w.installEventFilter(self)

    def _make(self):
        w = self.w
        dpr = w.devicePixelRatioF() or 1.0
        try:
            img = sky_image(w.width() * dpr, w.height() * dpr, self.idx, self.seed,
                            theme.BG1, bool(getattr(theme, "IS_LIGHT", False)))
            img.setDevicePixelRatio(dpr)
        except Exception as e:                       # noqa: BLE001 — the theme's own background then
            print(f"[library] sky: {e}", flush=True)
            img = None
        self.img = img
        w.update()

    def _step(self, t):
        self.t = t
        if t >= 1.0:
            self.old = None
        self.w.update()

    def eventFilter(self, o, e):
        t = e.type()
        if o is not self.w:
            return False
        if t == QEvent.Type.Resize:
            if self.img is None:
                self._make()
            else:
                self._timer.start()
        elif t == QEvent.Type.Show:
            if self.img is None:
                self._make()
            if motion.enabled() and self.img is not None:
                base = QImage(4, 4, QImage.Format.Format_RGB888)
                base.fill(QColor(theme.BG1))
                self.old, self.t = base, 0.0
                motion.run(self, 900, self._step, curve=QEasingCurve.Type.InOutSine)
        elif t == QEvent.Type.Paint and self.img is not None:
            p = QPainter(self.w)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            r = QRectF(self.w.rect())
            if self.old is not None and self.t < 1.0:
                p.drawImage(r, self.old)
                p.setOpacity(self.t)
            p.drawImage(r, self.img)
            p.end()
            return True
        return False


class SeedLibraryWindow(QDialog):
    """The Seed Library: the overview and a seed's page, one sliding over the other."""

    def __init__(self, win):
        # (19/11 bis) a window of its own (no owner): it can be minimised, has its own button in
        # the taskbar; it does not keep SSA running (the main window closes it)
        super().__init__(None)
        self.win = win
        self.setWindowTitle("Seed Library")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        try:
            self.setWindowIcon(win.windowIcon())
        except Exception:
            pass
        self.resize(1360, 860)
        self.setObjectName("SeedLibrary")
        try:
            renderer().palette = win.link.engine.palette
        except Exception:
            pass
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 14, 14, 14)
        self.stack = motion.FadeStack(slide=26, ms=260)
        self.over = Overview()
        self.over.open_seed.connect(self.show_seed)
        self.over.add_current.connect(self.keep_current)
        self.page = SeedPage()
        self.page.back.connect(self.show_overview)
        self.stack.addWidget(self.over)
        self.stack.addWidget(self.page)
        v.addWidget(self.stack)
        self._sky = None                         # QImage made for the current size
        self._sky_old = None                     # the previous one, faded out
        self._sky_t = 1.0
        self._sky_idx, self._sky_seed = pick_sky()
        self._sky_timer = QTimer(self)
        self._sky_timer.setSingleShot(True)
        self._sky_timer.setInterval(140)         # (resizing: the last picture, stretched, until it settles)
        self._sky_timer.timeout.connect(self._make_sky)
        theme.signals.changed.connect(lambda *_: self._make_sky())
        self.toast = Toast(self)
        install_noise_keys(self, self._noise_key)
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.add_seed)        # (20/11) Alt+T / H / C / E / W / Y / B, octaves

    def add_seed(self):
        """Ctrl+N / « Add a seed »: the dialog, from the overview."""
        if self.stack.currentWidget() is not self.over:
            self.show_overview()
        self.over.add_seed()

    def _noise_key(self, kind, *a):
        """A noise shortcut: to the mini-map under the mouse (else the last one used, else the
        only one) of the seed shown."""
        if self.stack.currentWidget() is not self.page or self.page.s is None:
            self.toast.say("Noise keys: on a seed's mini-maps")
            return
        maps = [b for b in self.page.board.blocks if isinstance(b, MapBlock)]
        if not maps:
            self.toast.say("No mini-map on this board  ·  « Mini-map » adds one")
            return
        cur = getattr(self.page.board, "current_map", None)
        blk = cur if cur in maps else maps[0]
        self.toast.say(blk.noise_key(kind, *a))

    # ── the sky ──────────────────────────────────────────────────────────────
    def new_sky(self):
        """Another sky (each time the library is opened), crossfaded from the last one."""
        self._sky_idx, self._sky_seed = pick_sky()
        old = self._sky
        self._make_sky()
        if old is not None and self.isVisible():
            self._sky_old = old
            motion.run(self, 650, self._sky_step, curve=QEasingCurve.Type.InOutSine)

    def intro(self):
        """The opening (19/11 bis): the window fades in, its sky blooms out of the plain
        background, the page comes in (side panel, bar, cards one by one)."""
        if not motion.enabled():
            return
        self.setWindowOpacity(0.0)
        motion.run(self, 260, lambda t: self.setWindowOpacity(t))
        base = QImage(4, 4, QImage.Format.Format_RGB888)
        base.fill(QColor(theme.BG1))
        if self._sky is not None:
            self._sky_old = base
            self._sky_t = 0.0
            motion.run(self, 1100, self._sky_step, curve=QEasingCurve.Type.InOutSine)
        pg = self.stack.currentWidget()
        if hasattr(pg, "intro"):
            pg.intro()

    def _sky_step(self, t):
        self._sky_t = t
        if t >= 1.0:
            self._sky_old = None
        self.update()

    def _make_sky(self):
        dpr = self.devicePixelRatioF() or 1.0
        try:
            img = sky_image(self.width() * dpr, self.height() * dpr, self._sky_idx, self._sky_seed,
                            theme.BG1, bool(getattr(theme, "IS_LIGHT", False)))
            img.setDevicePixelRatio(dpr)
        except Exception as e:                   # noqa: BLE001 — never a blank window: the theme's own
            print(f"[seed library] sky: {e}", flush=True)
            img = None
        self._sky = img
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._sky is None:
            self._make_sky()
        else:
            self._sky_timer.start()

    def paintEvent(self, e):
        if self._sky is None:
            return super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        if self._sky_old is not None and self._sky_t < 1.0:
            p.drawImage(r, self._sky_old)
            p.setOpacity(self._sky_t)
        p.drawImage(r, self._sky)
        p.end()

    def show_overview(self):
        self.over.refresh()
        self.stack.setCurrentWidget(self.over)

    def show_seed(self, sid):
        s = store().get(sid)
        if s is None:
            return
        self.page.load(s)
        self.stack.setCurrentWidget(self.page)
        self.page.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.stack.currentWidget() is self.page:
            self.show_overview()                     # Esc: back to the library first, then closes
            return
        super().keyPressEvent(e)

    def keep_current(self):
        w = self.win.world
        s = store().find(w.seed, w.version_name, w.large_biomes) or \
            store().add_seed(w.seed, w.version_name, w.large_biomes)
        self.show_seed(s["id"])

    def use_seed(self, s, at=None):
        """The seed (and its version, world type) goes to the top bar; at: also the map (True: its
        current view; (x, z, bpp): there)."""
        try:
            self.win.world.set(seed=int(s["seed"]), version_name=s.get("version") or None,
                               large_biomes=bool(s.get("large")))
        except Exception:                                  # noqa: BLE001 — a version gone: the seed only
            self.win.world.set(seed=int(s["seed"]))
        if at:
            self.win.show_page("map")
            if isinstance(at, tuple):
                try:
                    self.win.map.center_on(at[0], at[1], at[2])
                except Exception:
                    pass
            self.win.raise_()
            self.win.activateWindow()


def open_library(win, seed_id=None, add=False):
    """The window, made once per main window, shown (on a seed when given; add: then the
    « Add a seed » dialog)."""
    w = getattr(win, "_seed_library", None)
    opening = w is None or not w.isVisible()
    if w is None:
        w = win._seed_library = SeedLibraryWindow(win)
    elif opening:
        w.new_sky()                                  # each opening, its own sky
    if opening:
        w.over.rise_next = not seed_id
    if seed_id:
        w.show_seed(seed_id)
    else:
        w.show_overview()
    if w.isMinimized():
        w.showNormal()
    if opening:
        if motion.enabled():
            w.setWindowOpacity(0.0)
        w.show()
        QTimer.singleShot(0, w.intro)
    else:
        w.show()
    w.raise_()
    w.activateWindow()
    if add:
        QTimer.singleShot(420, w.add_seed)               # (after its opening)
    return w
