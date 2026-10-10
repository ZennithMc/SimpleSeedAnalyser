"""
qtui/map_engine.py — background producers for the map: biome tiles, noise
overlay tiles (climate noises, octaves, approximate height, exact Y), structure
positions and landmarks (spawn, strongholds).

Fluidity rules
  * everything heavy runs in a QThreadPool; each worker thread owns its own
    CubiomesLib / NoiseMapLib / terrain engine (thread-local, no shared lock);
  * tiles are requested centre-first with priorities (visible > prefetch ring);
  * a job whose tile has scrolled out of view before it starts is skipped, so a
    fast pan never leaves a backlog of useless work behind it;
  * finished tiles become QPixmaps in the GUI thread (fastest to draw);
  * noise tiles keep their raw 8-bit values: changing the noise palette only
    recolours them (instant), it never recomputes them.
Results are tagged with the world "epoch" and dropped if the seed / version
changed meanwhile.
"""
from __future__ import annotations

import concurrent.futures.process  # noqa: F401  (registers its atexit hook early, main thread)
import math
import os
import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

import cubiomes_wrap as cw
import noise_palettes as npal

TILE = 256                                   # cells per tile side
SCALES = (1, 4, 16, 64, 256)
CLIMATE = ("Temperature", "Humidity", "Continentalness", "Erosion", "Weirdness")
NOISES = CLIMATE + ("Approx height", "Exact Y")
EXACT_GRID = 64                              # exact-Y samples per tile side
_TLS = threading.local()

# value range → 0..255 (same ranges as the palette editor / regime labels)
HEIGHT_RANGE = npal.DISPLAY_RANGES["Height"]


def noise_range(noise):
    if noise in ("Approx height", "Exact Y"):
        return HEIGHT_RANGE
    return npal.NOISE_VALUE_RANGES.get(noise, (-1.0, 1.0))


# ── thread-local engines ────────────────────────────────────────────────────
def _thread_lib(world_key):
    lib = getattr(_TLS, "lib", None)
    if lib is None:
        lib = cw.make_worker_lib()
        _TLS.lib = lib
        _TLS.key = None
    if _TLS.key != world_key:
        seed, mc, large = world_key
        lib.setup_and_seed(mc, seed, large_biomes=large)
        _TLS.key = world_key
        _TLS.noise = {}
        _TLS.height = None
    return lib


def _thread_noise(world_key, noise):
    _thread_lib(world_key)
    d = _TLS.noise
    nm = d.get(noise)
    if nm is None:
        seed, _mc, large = world_key
        nm = cw.NoiseMapLib(seed, nptype=cw.NOISE_PARAMS[noise], large=large)
        d[noise] = nm
    return nm


def _thread_height(world_key):
    lib = _thread_lib(world_key)
    hm = getattr(_TLS, "height", None)
    if hm is None:
        hm = cw.HeightMapLib(lib)
        hm.set_seed(world_key[0])
        _TLS.height = hm
    return hm


def _thread_terrain(world_key):
    import terrain
    eng = getattr(_TLS, "terrain", None)
    if eng is None or getattr(_TLS, "terrain_key", None) != world_key:
        eng = terrain.TerrainEngine(world_key[0], world_key[1])
        _TLS.terrain = eng
        _TLS.terrain_key = world_key
    return eng


_GPU_LANE = threading.Lock()

# Stronghold eyes / top Y are pure Python (~5 ms each, GIL held): computed in a
# helper PROCESS so the UI never stutters after a seed change (26/09).
_EYE_POOL = None
_EYE_POOL_LOCK = threading.Lock()


def _eye_pool():
    global _EYE_POOL
    with _EYE_POOL_LOCK:
        if _EYE_POOL is None:
            import multiprocessing as mp
            from concurrent.futures import ProcessPoolExecutor
            _EYE_POOL = ProcessPoolExecutor(max_workers=2, mp_context=mp.get_context("spawn"))
            import atexit
            atexit.register(shutdown_eye_pool)
        return _EYE_POOL


def shutdown_eye_pool():
    """Called when the window closes: stop the helper processes cleanly (no
    'leaked semaphore' warnings at exit)."""
    global _EYE_POOL
    with _EYE_POOL_LOCK:
        pool, _EYE_POOL = _EYE_POOL, None
    if pool is not None:
        try:
            pool.shutdown(wait=True, cancel_futures=True)
        except Exception:
            pass


def _exact_grid(world_key, x0, z0, n, step, caves):
    """Exact surface grid. The GPU lane is taken when free (fastest); when it is
    busy the calling worker computes on the CPU instead of waiting, so all the
    exact-Y workers run in parallel."""
    import terrain
    if _GPU_LANE.acquire(blocking=False):
        try:
            if terrain._gpu() is not None:
                return _thread_terrain(world_key).surface_grid(x0, z0, n, n, step=step, caves=caves)
        finally:
            _GPU_LANE.release()
    return terrain.surface_grid_exact(world_key[1], world_key[0], x0, z0, n, n,
                                      step=step, caves=caves)


# ── palettes ────────────────────────────────────────────────────────────────
_LUTS: dict = {}


def palette_names():
    from colormaps import get_available_colormaps
    return get_available_colormaps()


def biome_lut(palette: str) -> np.ndarray:
    lut = _LUTS.get(palette)
    if lut is None:
        from colormaps import load_colormap
        colors = load_colormap(palette)
        n = max(max(colors), max(cw.BIOME_COLORS)) + 1
        lut = np.empty((max(n, 256), 3), np.uint8)
        lut[:] = cw.DEFAULT_COLOR
        for bid, rgb in colors.items():
            if 0 <= bid < len(lut):
                lut[bid] = rgb
        _LUTS[palette] = lut
    return lut


def noise_lut(noise: str, palette: str) -> np.ndarray:
    """LUT on the EXTENDED colour axis (noise_palettes.axis): the noise tiles
    store u = u_from_pos(value) so extremes beyond the usual range get their
    own colours (custom palettes with stops out there)."""
    key = ("noise", noise, palette)
    lut = _LUTS.get(key)
    if lut is None:
        en = npal.editor_noise(noise)
        if palette == "Grayscale" and en == "Height":
            lut = npal.grayscale_height_lut()                  # = cubiomes-viewer (08/10)
        elif palette in npal.CUSTOM_PALETTES:                  # per-noise custom gradient
            lut = npal.lut_from_stops(npal.CUSTOM_PALETTES[palette].get(en, []), en)
        else:
            lut = npal.warp_lut(npal.builtin_lut(palette, en), en)
        _LUTS[key] = lut
    return lut


def _pixmap_from_rgb(rgb: np.ndarray) -> QImage:
    h, w = rgb.shape[:2]
    rgb = np.ascontiguousarray(rgb)
    return QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


class _Bus(QObject):
    tile = Signal(object, object)        # (key, QImage)
    preview = Signal(object, object)     # (key, QImage) quick 1/16 version
    noise = Signal(object, object)       # (key, uint8 array)
    structures = Signal(object, object)
    landmarks = Signal(object, object)
    pieces = Signal(object, object)      # (key, stronghold pieces)
    skipped = Signal(object)
    failed = Signal(object)


class _Job(QRunnable):
    def __init__(self, fn, on_fail=None):
        super().__init__()
        self.fn = fn
        self.on_fail = on_fail                    # frees the tile's "pending" slot
        self.setAutoDelete(True)

    def _fail(self):
        if self.on_fail is not None:
            try:
                self.on_fail()
            except RuntimeError:
                pass

    def run(self):
        try:
            self.fn()
        except RuntimeError as e:                 # window closed while the job ran
            if "deleted" not in str(e):
                import traceback
                traceback.print_exc()
                self._fail()
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"[map] job error: {e}", flush=True)
            # before: the key stayed "pending" for ever → "Generating… N jobs
            # left" never ended (e.g. Exact Y without the terrain library)
            self._fail()


def octave_arrays(nm, gx, gz, W, H, scale, octave):
    """Noise grid for an octave spec: None = full noise; (pair, incl_b, single)."""
    if octave is None or not getattr(nm, "_has_octaves", False):
        return nm.gen_noise_grid(gx, gz, W, H, scale)
    pair, inclb, single = octave
    cur = nm.gen_noise_grid_octaves(gx, gz, W, H, scale, pair, inclb)
    if not single or (pair == 0 and not inclb):
        return cur
    if inclb:
        prev = nm.gen_noise_grid_octaves(gx, gz, W, H, scale, pair, False)
    else:
        prev = nm.gen_noise_grid_octaves(gx, gz, W, H, scale, pair - 1, True)
    return cur - prev


class MapEngine(QObject):
    tileReady = Signal()
    structuresReady = Signal()
    landmarksReady = Signal()

    MAX_TILES = 900
    MAX_NOISE = 700

    def __init__(self, world):
        super().__init__()
        self.world = world
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(2, min(12, (os.cpu_count() or 4) - 1)))
        # exact Y: heavy. Separate pool so it never starves the biome tiles.
        # The CUDA terrain lib keeps global state (one lane, guarded by
        # _GPU_LANE); every other worker computes on the CPU with its own
        # thread-local C context, which is thread-safe and scales per core.
        self.exact_pool = QThreadPool()
        self.exact_pool.setMaxThreadCount(max(2, min(10, (os.cpu_count() or 4) // 2)))
        self.bus = _Bus()
        self.bus.tile.connect(self._on_tile)
        self.bus.preview.connect(self._on_preview)
        self.bus.noise.connect(self._on_noise)
        self.bus.structures.connect(self._on_structures)
        self.bus.landmarks.connect(self._on_landmarks)
        self.bus.pieces.connect(self._on_pieces)
        self.sh_pieces: dict = {}
        self.pieces_pending: set = set()
        self.bus.skipped.connect(self._on_skipped)
        self.bus.failed.connect(self._on_failed)
        self.failed: set = set()                 # keys whose job raised (not retried this epoch)
        self.palette = "True Colors (ShowMe7)"
        self.tiles: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        self.previews: "OrderedDict[tuple, QPixmap]" = OrderedDict()   # quick versions
        self.pending: set = set()
        self.noise_raw: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self.noise_pix: "OrderedDict[tuple, QPixmap]" = OrderedDict()
        self.structs: dict = {}
        self.struct_pending: set = set()
        self.zombies: dict = {}             # village block key → frozenset of abandoned (x, z)
        self.landmarks: dict = {}
        self.wanted: set = set()                 # keys needed by the last frame
        self._single_range: dict = {}
        self.epoch = 0
        world.changed.connect(self.reset)
        self.reset()

    # ── frame bookkeeping (fluidity) ────────────────────────────────────────
    def begin_frame(self):
        self._frame_wanted = set()

    def end_frame(self):
        self.wanted = self._frame_wanted       # atomic swap, read by workers

    def _want(self, key):
        self._frame_wanted.add(key)

    def _still_wanted(self, key) -> bool:
        return key in self.wanted or key in getattr(self, "_frame_wanted", ())

    # ── world / palette changes ─────────────────────────────────────────────
    def reset(self):
        self.epoch += 1
        self.tiles.clear(); self.pending.clear(); self.previews.clear()
        self.noise_raw.clear(); self.noise_pix.clear()
        self.structs.clear(); self.struct_pending.clear(); self.zombies = {}
        self.basements = {}
        self.sh_pieces = {}; self.pieces_pending = set()
        self.landmarks = {}
        self._request_landmarks()

    def set_palette(self, name: str):
        if name != self.palette:
            self.palette = name
            self.tiles.clear()
            self.previews.clear()

    def min_scale(self) -> int:
        return 1 if self.world.mc >= cw.MC_VERSIONS["Java 1.18"] else 4

    def pending_count(self) -> int:
        return len(self.pending) + len(self.struct_pending)

    def _on_skipped(self, key):
        self.pending.discard(key)
        # (08/10) a job skipped because a frame in between (zoom animation) did
        # not want it, while the LAST frame does: nothing repainted, so it was
        # never asked again — the map stayed on the blurry coarser tile until
        # the mouse moved. Ask for a repaint: the next frame requests it again.
        if key in self.wanted or key in getattr(self, "_frame_wanted", ()):
            self.tileReady.emit()

    def _on_failed(self, key):
        self.pending.discard(key)
        self.failed.add(key)
        if len(self.failed) > 5000:
            self.failed.clear()

    # ── biome tiles ─────────────────────────────────────────────────────────
    def tile(self, scale, tx, tz, priority=5, fetch=True):
        key = (self.epoch, "b", self.palette, scale, tx, tz)
        pm = self.tiles.get(key)
        if pm is not None:
            self.tiles.move_to_end(key)
            return pm
        if not fetch or key in self.failed:
            return None
        self._want(key)
        if key not in self.pending:
            self.pending.add(key)
            wk, pal = self.world.key, self.palette

            def job(key=key, wk=wk, pal=pal):
                if not self._still_wanted(key):
                    self.bus.skipped.emit(key)
                    return
                lib = _thread_lib(wk)
                ids = np.frombuffer(lib.gen_biomes_region(tx * TILE, tz * TILE, TILE, TILE, scale),
                                    dtype=np.int32).reshape(TILE, TILE)
                lut = biome_lut(pal)
                self.bus.tile.emit(key, _pixmap_from_rgb(lut[np.clip(ids, 0, len(lut) - 1)]))
            self.pool.start(_Job(job, lambda k=key: self.bus.failed.emit(k)), priority)
            # Fluidity: a quick version first (the same area at a 4x coarser
            # scale, 16x fewer cells) so an empty screen fills at once; the
            # exact tile replaces it when ready.
            ps = scale * 4
            if ps in SCALES and key not in self.previews:
                def pjob(key=key, wk=wk, pal=pal, ps=ps):
                    if key in self.tiles or not self._still_wanted(key):
                        return
                    lib = _thread_lib(wk)
                    n = TILE // 4
                    ids = np.frombuffer(lib.gen_biomes_region(tx * n, tz * n, n, n, ps),
                                        dtype=np.int32).reshape(n, n)
                    lut = biome_lut(pal)
                    self.bus.preview.emit(key, _pixmap_from_rgb(lut[np.clip(ids, 0, len(lut) - 1)]))
                self.pool.start(_Job(pjob), priority + 3)
        return None

    def preview(self, scale, tx, tz):
        """Quick coarse version of a biome tile still generating (or None)."""
        return self.previews.get((self.epoch, "b", self.palette, scale, tx, tz))

    def _on_preview(self, key, img):
        if key[0] != self.epoch or key[2] != self.palette or key in self.tiles:
            return
        self.previews[key] = QPixmap.fromImage(img)
        while len(self.previews) > self.MAX_TILES:
            self.previews.popitem(last=False)
        self.tileReady.emit()

    def cached_tile(self, scale, tx, tz):
        return self.tiles.get((self.epoch, "b", self.palette, scale, tx, tz))

    def _on_tile(self, key, img):
        self.pending.discard(key)
        if key[0] != self.epoch or key[2] != self.palette:
            return
        self.tiles[key] = QPixmap.fromImage(img)
        self.previews.pop(key, None)
        while len(self.tiles) > self.MAX_TILES:
            self.tiles.popitem(last=False)
        self.tileReady.emit()

    # ── noise overlay tiles ────────────────────────────────────────────────
    def noise_tile(self, spec, npalette, scale, tx, tz, priority=4, fetch=True):
        """spec = (noise, octave, caves). Returns a coloured QPixmap or None."""
        rkey = (self.epoch, "n", spec, scale, tx, tz)
        pkey = rkey + (npalette,)
        pm = self.noise_pix.get(pkey)
        if pm is not None:
            self.noise_pix.move_to_end(pkey)
            return pm
        raw = self.noise_raw.get(rkey)
        if raw is not None:                       # recolour only (palette change)
            lut = noise_lut(spec[0], npalette)
            pm = QPixmap.fromImage(_pixmap_from_rgb(lut[raw]))
            self.noise_pix[pkey] = pm
            while len(self.noise_pix) > self.MAX_NOISE:
                self.noise_pix.popitem(last=False)
            return pm
        if not fetch or rkey in self.failed:
            return None
        self._want(rkey)
        if rkey not in self.pending:
            self.pending.add(rkey)
            wk = self.world.key
            job = self._noise_job(rkey, wk, spec, scale, tx, tz)
            fail = (lambda k=rkey: self.bus.failed.emit(k))
            if spec[0] == "Exact Y":
                self.exact_pool.start(_Job(job, fail), priority)
            else:
                self.pool.start(_Job(job, fail), priority)
        return None

    def palette_edited(self, name):
        """A custom noise palette changed: recolour its tiles from the cached raw
        values (no noise is recomputed)."""
        for k in [k for k in _LUTS if isinstance(k, tuple) and len(k) == 3 and k[2] == name]:
            _LUTS.pop(k, None)
        for k in [k for k in self.noise_pix if k[-1] == name]:
            self.noise_pix.pop(k, None)
        self.tileReady.emit()

    def cached_noise(self, spec, npalette, scale, tx, tz):
        return self.noise_tile(spec, npalette, scale, tx, tz, fetch=False)

    def _noise_job(self, rkey, wk, spec, scale, tx, tz):
        noise, octave, caves = spec

        def job():
            if not self._still_wanted(rkey):
                self.bus.skipped.emit(rkey)
                return
            if noise in CLIMATE:
                nm = _thread_noise(wk, noise)
                arr = octave_arrays(nm, tx * TILE, tz * TILE, TILE, TILE, scale, octave)
                if octave is not None and octave[2]:            # single octave
                    # one shared range per (noise, octave) → no seams between tiles
                    rk = (self.epoch, noise, octave)
                    m = self._single_range.get(rk)
                    if m is None:
                        m = max(1e-6, float(np.abs(arr).max()) * 1.1)
                        m = self._single_range.setdefault(rk, m)
                    vmin, vmax = -m, m
                else:
                    vmin, vmax = noise_range(noise)
            elif noise == "Approx height":
                arr = self._height_tile(wk, scale, tx, tz)
                vmin, vmax = HEIGHT_RANGE
            elif int(wk[1]) in (19, 20):                        # Exact Y 1.16 (10/10 bis)
                # the exact 1.16.1 terrain (chunks rebuilt): every block at 1:1
                # (MapView._tile_scale allows 1:1 for this layer), 32 samples a
                # side further out (one chunk each)
                n = TILE if scale == 1 else 32
                step = max(1, (TILE * scale) // n)
                import feature116 as f116
                small = f116.surface_grid(wk[1], wk[0], tx * TILE * scale, tz * TILE * scale,
                                          n + 1, n + 1, step, bool(caves)).astype(np.float32)
                arr = small[:TILE, :TILE] if n == TILE else _bilinear_up(small, n, TILE)
                vmin, vmax = HEIGHT_RANGE
            else:                                               # Exact Y
                # 1:1 tiles: every block (08/10). The terrain is generated per
                # chunk, so one sample per block costs about what one per 4 blocks
                # did (measured: 1.35 s vs 1.28 s per tile) — zoomed in, the map
                # showed 4-block bilinear blur. Coarser scales keep 64 samples per
                # side (each sample there is in its own chunk: 4× the time for 2×).
                n = TILE if scale == 1 else min(EXACT_GRID, TILE)
                step = max(1, (TILE * scale) // n)
                small = _exact_grid(wk, tx * TILE * scale, tz * TILE * scale, n + 1, step,
                                    bool(caves)).astype(np.float32)
                arr = _bilinear_up(small, n, TILE)
                vmin, vmax = HEIGHT_RANGE
            pos = (np.asarray(arr, np.float32) - vmin) / max(vmax - vmin, 1e-6)
            u = npal.u_from_pos(pos, npal.editor_noise(noise))       # extended axis
            raw = np.clip(np.rint(np.asarray(u) * 255.0), 0, 255).astype(np.uint8)
            if noise in ("Approx height", "Exact Y"):
                # the sinkhole level (y ≤ −64, red in Grayscale like cubiomes-viewer)
                # gets its own colour indices: exact threshold on every palette
                sk = npal.sink_index()
                a_ = np.asarray(arr)
                raw = np.where(a_ <= npal.SINK_Y, np.minimum(raw, sk),
                               np.maximum(raw, sk + 1)).astype(np.uint8)
            self.bus.noise.emit(rkey, raw)
        return job

    def _height_tile(self, wk, scale, tx, tz):
        hm = _thread_height(wk)
        if scale == 1 and int(wk[1]) < cw.MC_VERSIONS["Java 1.18"]:
            # (10/10 bis) zoomed in before 1.18: the 1:4 heights are blended
            # between the cell corners (bilinear, like the game blends its
            # noise) instead of 4-block squares
            n = TILE // 4
            g, _ = hm.gen_height_grid(tx * n, tz * n, n + 1, n + 1, step=1)
            return _bilinear_up(np.asarray(g, np.float32), n, TILE).astype(np.float32)
        # (10/10 bis) one sample per pixel at every scale: the stride used to be
        # capped at 32, so a 1:256 tile computed 528² samples (10 s) and a
        # 1:16 / 1:64 one 2-3 s — zoomed out, the height map seemed stuck
        OVL = 8
        stride = max(1, scale // 4)
        n_samp = (TILE * scale) // (stride * 4)
        gx4, gz4 = tx * TILE * scale // 4, tz * TILE * scale // 4
        gen_n = n_samp + 2 * OVL
        full, _ = hm.gen_height_grid(gx4 - OVL * stride, gz4 - OVL * stride, gen_n, gen_n, step=stride)
        samp = np.asarray(full[OVL:OVL + n_samp, OVL:OVL + n_samp], np.float32)
        if n_samp == TILE:
            return samp
        from PIL import Image
        return np.asarray(Image.fromarray(samp, mode="F").resize((TILE, TILE), Image.BILINEAR),
                          np.float32)

    def _on_noise(self, key, raw):
        self.pending.discard(key)
        if key[0] != self.epoch:
            return
        self.noise_raw[key] = raw
        while len(self.noise_raw) > self.MAX_NOISE:
            self.noise_raw.popitem(last=False)
        self.tileReady.emit()

    # ── structures ─────────────────────────────────────────────────────────
    STRUCT_BLOCK = 4096

    # structure-seed view (Alt+S, like cubiomes-viewer, 03/10): every POTENTIAL
    # position of each structure (its region's attempt), biomes ignored — what
    # the 48 low bits alone give, the same for the 65 536 seeds of the family
    seed_view = False

    def set_seed_view(self, on):
        self.seed_view = bool(on)
        self.structuresReady.emit()

    def structures(self, skey: str, bx: int, bz: int, fetch=True):
        sv = self.seed_view
        key = (self.epoch, skey, bx, bz) + (("seed",) if sv else ())
        v = self.structs.get(key)
        if v is not None or not fetch:
            return v
        if key not in self.struct_pending:
            self.struct_pending.add(key)
            stype = cw.STRUCTURE_TYPES[skey]
            wk = self.world.key
            B = self.STRUCT_BLOCK

            def job(key=key, wk=wk):
                lib = _thread_lib(wk)
                pts = lib.find_structures_in_box(stype, bx * B, bz * B, bx * B + B - 1, bz * B + B - 1,
                                                 verify_biome=not sv, max_count=100000)
                if skey == "igloo" and pts:          # igloos with a basement: own icon
                    try:
                        self.basements[key] = frozenset(
                            q for q in pts if lib.get_variant_basement(stype, q[0], q[1]))
                    except Exception:
                        pass
                if skey == "village" and pts and not sv:
                    try:                     # abandoned (zombie) villages: their own icon
                        flags = lib.village_abandoned_flags(pts)
                        self.zombies[key] = frozenset(p for p, f in zip(pts, flags) if f)
                    except Exception:
                        pass
                self.bus.structures.emit(key, pts)
            self.pool.start(_Job(job), 2)
        return None

    # ── (10/10) 1.16 dungeons / lava lakes, per 512-block block ──────────────
    FEAT_BLOCK = 256         # (10/10 bis) exact terrain: ~1 s of one core per block

    def features116(self, kind: str, bx: int, bz: int, fetch=True):
        """(feats, {(ox, oz): lava columns}) of a FEAT_BLOCK block, or None while
        computed. The structure-seed view (Alt+S) shows what the 48 bits give
        (attempts, lakes of both indices); the normal view the features that
        really generate (exact 1.16.1 terrain, feature116.features_exact: one
        pass for both kinds, on the dedicated f116 pool, one block per worker)."""
        sv = self.seed_view
        key = (self.epoch, "f116", kind, bx, bz) + (("seed",) if sv else ())
        v = self.structs.get(key)
        if v is not None or not fetch:
            return v
        if key in self.struct_pending:
            return None
        seed, mc, _large = self.world.key
        B = self.FEAT_BLOCK
        if sv:
            self.struct_pending.add(key)

            def job(key=key):
                import feature116 as f116
                try:
                    fs = f116.features(mc, seed, kind, bx * B, bz * B, bx * B + B - 1, bz * B + B - 1,
                                       exact=False)
                    cols = {}
                    if kind == "lava_lake":
                        for f in fs:
                            if not f.surface:
                                cols[(f.ox, f.oz)] = f116.lake_columns(seed, f)
                    else:                    # attempts above the approximate ground
                        keep = set(f116.below_ground(mc, seed, fs))
                        cols = {"above": frozenset(f for f in fs if f not in keep)}
                except Exception:
                    fs, cols = [], {}
                self.bus.structures.emit(key, (fs, cols))
            self.pool.start(_Job(job), 2)
            return None
        kd = (self.epoch, "f116", "dungeon", bx, bz)
        kl = (self.epoch, "f116", "lava_lake", bx, bz)
        self.struct_pending.add(kd)
        self.struct_pending.add(kl)
        if not hasattr(self, "f116_pool"):
            self.f116_pool = QThreadPool()
            self.f116_pool.setMaxThreadCount(max(2, (os.cpu_count() or 4) - 2))
        cx_, cz_ = getattr(self, "f116_centre", (0, 0))
        prio = max(0, 50 - int(math.hypot(bx * B + B / 2 - cx_, bz * B + B / 2 - cz_) / B))

        def jobx(kd=kd, kl=kl):
            import feature116 as f116
            if kd[0] != self.epoch:          # another seed / version since: dropped
                return
            try:
                dun, lak = f116.features_exact(mc, seed, bx * B, bz * B, bx * B + B - 1, bz * B + B - 1,
                                               threads=1)
                cols = {(f.ox, f.oz): f116.lake_columns(seed, f) for f in lak}
            except Exception:
                dun, lak, cols = (), (), {}
            self.bus.structures.emit(kd, (list(dun), {}))
            self.bus.structures.emit(kl, (list(lak), cols))
        self.f116_pool.start(_Job(jobx), prio)
        return None

    def with_basement(self, bx: int, bz: int):
        """Igloos with a basement of a structure block (empty until computed);
        it depends on the seed only, so the structure-seed view has it too."""
        key = (self.epoch, "igloo", bx, bz) + (("seed",) if self.seed_view else ())
        return self.basements.get(key, frozenset())

    def abandoned(self, bx: int, bz: int):
        """Abandoned villages of a structure block (empty until computed)."""
        if self.seed_view:                   # depends on the biome: not in seed view
            return frozenset()
        return self.zombies.get((self.epoch, "village", bx, bz), frozenset())

    def _on_structures(self, key, pts):
        self.struct_pending.discard(key)
        if key[0] != self.epoch:
            return
        self.structs[key] = pts
        self.structuresReady.emit()

    # ── stronghold pieces (zoomed-in map, 03/10) ────────────────────────────
    def stronghold_pieces(self, x, z, fetch=True):
        """Every piece of the stronghold at (x, z), final Y (helper process)."""
        key = (self.epoch, int(x), int(z))
        v = self.sh_pieces.get(key)
        if v is not None or not fetch or key in self.pieces_pending:
            return v
        self.pieces_pending.add(key)
        wk = self.world.key

        def job():
            import stronghold_eyes as se
            try:
                res = _eye_pool().submit(se.stronghold_pieces_batch, wk[0], wk[1],
                                         [(key[1], key[2])]).result()
            except Exception:                    # pool closing / failure: nothing drawn
                res = {}
            self.bus.pieces.emit(key, res.get((key[1], key[2]), []))
        self.pool.start(_Job(job), 3)
        return None

    def _on_pieces(self, key, pieces):
        self.pieces_pending.discard(key)
        if key[0] != self.epoch:
            return
        self.sh_pieces[key] = pieces
        self.structuresReady.emit()

    # ── spawn + strongholds ─────────────────────────────────────────────────
    def _request_landmarks(self):
        epoch, wk = self.epoch, self.world.key

        def job():
            lib = _thread_lib(wk)
            out = {"spawn": lib.get_spawn()}
            try:
                out["strongholds"] = lib.find_strongholds(max_count=128)
            except Exception:
                out["strongholds"] = []
            self.bus.landmarks.emit(epoch, dict(out))
            # eyes + top Y of every stronghold (≈ 5 ms each, 128 → < 1 s),
            # published by rings so the nearest ones show up first
            try:
                import stronghold_eyes as se
                info = {}
                shs = out["strongholds"]
                pool = _eye_pool()
                bounds = [b for b in (3, 9, 21, 36, 56, 83, 128) if b <= len(shs)] or [len(shs)]
                if bounds[-1] < len(shs):
                    bounds.append(len(shs))
                lo = 0
                for hi in bounds:                      # ring by ring, nearest first
                    if epoch != self.epoch:
                        return
                    try:
                        info.update(pool.submit(se.eyes_maxy_batch, wk[0], wk[1],
                                                shs[lo:hi]).result())
                    except RuntimeError:            # helper pool shut down (window closing)
                        return
                    lo = hi
                    if epoch != self.epoch:
                        return
                    self.bus.landmarks.emit(epoch, dict(out, sh_info=dict(info)))
            except Exception:
                if epoch == self.epoch and not getattr(threading, "_SHUTTING_DOWN", False):
                    import sys
                    if not sys.is_finalizing():
                        import traceback
                        traceback.print_exc()
        self.pool.start(_Job(job), 20)

    def _on_landmarks(self, epoch, data):
        if epoch == self.epoch:
            self.landmarks = data
            self.landmarksReady.emit()


def _bilinear_up(small: np.ndarray, n: int, size: int) -> np.ndarray:
    """(n+1)² corner samples → size² pixels, corner-exact (no tile seams)."""
    if n == size:
        return small[:size, :size]
    t = (np.arange(size, dtype=np.float32) * n) / size
    i0 = np.floor(t).astype(np.int32)
    i1 = np.minimum(i0 + 1, n)
    f = (t - i0).astype(np.float32)
    r = small[i0] * (1.0 - f)[:, None] + small[i1] * f[:, None]
    return r[:, i0] * (1.0 - f)[None, :] + r[:, i1] * f[None, :]
