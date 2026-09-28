"""
qtui/draw_model.py — the drawing being edited on the Draw page.

A drawing = a W×H grid of class codes (draw_engine: LAND / OCEAN / … /
biome_code / group_code, 0 = not painted) + a weight per cell, placed in the
world: `cell` blocks per cell, centred on `center`, allowed to slide up to
`slide` cells in every direction.

Every edit goes through `begin()` … `commit(label)` so that undo / redo
always restore the grid AND the weights together (old bug D11: weights were
lost by Undo / Clear).
"""
from __future__ import annotations

import base64
import datetime
import json
import zlib
from collections import deque

import numpy as np
from PySide6.QtCore import QObject, Signal

import draw_engine as de

MAX_SIDE = 256          # cells per side (engine limit 512; UI stays responsive)
MIN_SIDE = 1
HISTORY = 200
CELL_SIZES = (16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512)
WEIGHTS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
FORMAT = "ssa-drawing"
VERSION = 1


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _pack(a: np.ndarray) -> str:
    return base64.b64encode(zlib.compress(np.ascontiguousarray(a).tobytes(), 6)).decode("ascii")


def _unpack(s: str, dtype, shape) -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(s))
    a = np.frombuffer(raw, dtype=dtype).copy()
    if a.size != shape[0] * shape[1]:
        raise ValueError("drawing data size does not match its dimensions")
    return a.reshape(shape)


def resample(a: np.ndarray, H: int, W: int, mode: str = "nearest") -> np.ndarray:
    """Resize a (h, w) grid. nearest = keep codes (shape preserved);
    mean = average (weights)."""
    h, w = a.shape
    if (h, w) == (H, W):
        return a.copy()
    if mode == "mean" and H <= h and W <= w:
        ys = np.linspace(0, h, H + 1).astype(int)
        xs = np.linspace(0, w, W + 1).astype(int)
        out = np.empty((H, W), a.dtype)
        for j in range(H):
            for i in range(W):
                blk = a[ys[j]:max(ys[j] + 1, ys[j + 1]), xs[i]:max(xs[i] + 1, xs[i + 1])]
                out[j, i] = blk.mean()
        return out
    jj = np.minimum(h - 1, ((np.arange(H) + 0.5) * h / H).astype(int))
    ii = np.minimum(w - 1, ((np.arange(W) + 0.5) * w / W).astype(int))
    return a[jj][:, ii].copy()


def majority_downsample(g: np.ndarray, H: int, W: int) -> np.ndarray:
    """Codes of a (h, w) grid reduced to (H, W): the most painted code of
    each block (unpainted only when the block is mostly unpainted)."""
    h, w = g.shape
    if H >= h and W >= w:
        return resample(g, H, W)
    ys = np.linspace(0, h, H + 1).astype(int)
    xs = np.linspace(0, w, W + 1).astype(int)
    out = np.zeros((H, W), np.int32)
    for j in range(H):
        for i in range(W):
            blk = g[ys[j]:max(ys[j] + 1, ys[j + 1]), xs[i]:max(xs[i] + 1, xs[i + 1])].ravel()
            vals, cnt = np.unique(blk, return_counts=True)
            k = int(np.argmax(cnt))
            out[j, i] = vals[k]
    return out


def _disc(r: float):
    """Offsets of a disc of diameter ~2r+1 (brush footprint)."""
    R = int(np.ceil(r))
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    m = xx * xx + yy * yy <= r * r + 0.25
    return yy[m], xx[m]


class DrawModel(QObject):
    changed = Signal(str)        # "paint" | "geometry" | "load" | "meta" | "history"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.W = self.H = 48
        self.grid = np.zeros((48, 48), np.int32)
        self.weights = np.ones((48, 48), np.float32)
        self.cell = 64
        self.center = (0, 0)
        self.slide = 12
        self.balance = False
        self.name = "Untitled drawing"
        self.description = ""
        self.created = now_iso()
        self.path = None                       # library entry, if any
        self.dirty = False
        self._undo = deque(maxlen=HISTORY)
        self._redo = []
        self._pending = None                   # snapshot taken by begin()
        self._depth = 0                        # nested begin() / commit()

    # ── state snapshots (undo / redo) ───────────────────────────────────────
    def _snap(self):
        return (self.grid.copy(), self.weights.copy(), self.W, self.H, self.cell,
                tuple(self.center), self.slide)

    def _restore(self, s):
        g, w, W, H, cell, center, slide = s
        geo = (W, H, cell, center, slide) != (self.W, self.H, self.cell, tuple(self.center), self.slide)
        self.grid, self.weights = g.copy(), w.copy()
        self.W, self.H, self.cell, self.center, self.slide = W, H, cell, tuple(center), slide
        self.dirty = True
        self.changed.emit("geometry" if geo else "paint")

    def begin(self, label: str | None = None):
        """Start an edit (a stroke, a fill…). Nested begin / commit pairs make
        ONE undo step, named by the outermost label."""
        self._depth += 1
        if self._pending is None:
            self._pending = (self._snap(), label)

    def commit(self, label: str = "Edit"):
        if self._pending is None:
            self._depth = 0
            return
        self._depth = max(0, self._depth - 1)
        if self._depth > 0:
            return
        snap, outer = self._pending
        label = outer or label
        self._pending = None
        g, w, W, H, cell, center, slide = snap
        same = (W, H, cell, tuple(center), slide) == (self.W, self.H, self.cell,
                                                      tuple(self.center), self.slide) \
            and g.shape == self.grid.shape and np.array_equal(g, self.grid) \
            and np.array_equal(w, self.weights)
        if same:
            return
        self._undo.append((label, snap))
        self._redo.clear()
        self.dirty = True
        self.changed.emit("history")

    def flush(self):
        """Close an edit left open (a stroke whose mouse release was lost)."""
        if self._pending is not None:
            self._depth = 1
            self.commit("Edit")

    def cancel(self):
        self._depth = 0
        if self._pending is not None:
            snap, _ = self._pending
            self._pending = None
            self._restore(snap)

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo_text(self):
        return self._undo[-1][0] if self._undo else ""

    def redo_text(self):
        return self._redo[-1][0] if self._redo else ""

    def undo(self):
        if self._pending is not None:
            self._depth = 1
            self.commit("Edit")
        if not self._undo:
            return False
        label, snap = self._undo.pop()
        self._redo.append((label, self._snap()))
        self._restore(snap)
        self.changed.emit("history")
        return True

    def redo(self):
        if not self._redo:
            return False
        label, snap = self._redo.pop()
        self._undo.append((label, self._snap()))
        self._restore(snap)
        self.changed.emit("history")
        return True

    # ── painting primitives (cells; call inside begin / commit) ─────────────
    def inside(self, i, j):
        return 0 <= i < self.W and 0 <= j < self.H

    def _put(self, jj, ii, code=None, weight=None):
        m = (ii >= 0) & (ii < self.W) & (jj >= 0) & (jj < self.H)
        jj, ii = jj[m], ii[m]
        if not len(ii):
            return False
        if code is not None:
            self.grid[jj, ii] = code
            if code == de.ANY:
                self.weights[jj, ii] = 1.0          # erasing forgets the weight
        if weight is not None:
            painted = self.grid[jj, ii] != de.ANY    # weights only on painted cells
            self.weights[jj[painted], ii[painted]] = weight
        return True

    def stamp(self, i, j, radius, code=None, weight=None):
        dy, dx = _disc(radius)
        if self._put(dy + int(j), dx + int(i), code, weight):
            self.changed.emit("paint")

    def line(self, i0, j0, i1, j1, radius, code=None, weight=None):
        """Brush from (i0, j0) to (i1, j1): no gap however fast the mouse."""
        n = int(max(abs(i1 - i0), abs(j1 - j0))) + 1
        ts = np.linspace(0.0, 1.0, n + 1)
        ci = np.rint(i0 + (i1 - i0) * ts).astype(int)
        cj = np.rint(j0 + (j1 - j0) * ts).astype(int)
        dy, dx = _disc(radius)
        jj = (cj[:, None] + dy[None, :]).ravel()
        ii = (ci[:, None] + dx[None, :]).ravel()
        if self._put(jj, ii, code, weight):
            self.changed.emit("paint")

    def shape(self, kind, i0, j0, i1, j1, code=None, weight=None, filled=True, radius=0.5):
        a0, a1 = sorted((int(i0), int(i1)))
        b0, b1 = sorted((int(j0), int(j1)))
        jj, ii = np.mgrid[b0:b1 + 1, a0:a1 + 1]
        if kind == "ellipse":
            cx, cy = (a0 + a1) / 2.0, (b0 + b1) / 2.0
            rx, ry = max(0.5, (a1 - a0) / 2.0 + 0.5), max(0.5, (b1 - b0) / 2.0 + 0.5)
            d = ((ii - cx) / rx) ** 2 + ((jj - cy) / ry) ** 2
            m = d <= 1.0
            if not filled:
                inner = ((ii - cx) / max(0.5, rx - 1.2 * radius)) ** 2 + \
                        ((jj - cy) / max(0.5, ry - 1.2 * radius)) ** 2
                m &= inner > 1.0
        else:
            m = np.ones(ii.shape, bool)
            if not filled:
                t = max(1, int(round(radius)))
                m = (ii < a0 + t) | (ii > a1 - t) | (jj < b0 + t) | (jj > b1 - t)
        if self._put(jj[m], ii[m], code, weight):
            self.changed.emit("paint")

    def circle(self, ci, cj, rad, code=None, weight=None, filled=True, width=1.0):
        """Circle centred on cell (ci, cj), radius rad cells."""
        R = int(np.ceil(rad)) + 1
        jj, ii = np.mgrid[int(cj) - R:int(cj) + R + 1, int(ci) - R:int(ci) + R + 1]
        d2 = (ii - ci) ** 2 + (jj - cj) ** 2
        m = d2 <= rad * rad + 0.25 + rad * 0.5
        if not filled:
            inner = max(0.0, rad - width)
            m &= d2 > inner * inner + inner * 0.5
        if self._put(jj[m], ii[m], code, weight):
            self.changed.emit("paint")

    def fill(self, i, j, code=None, weight=None):
        """Flood fill (4-connected) of the region of (i, j)'s code."""
        if not self.inside(i, j):
            return
        target = int(self.grid[j, i])
        if code is not None and target == code:
            return
        if weight is not None and target == de.ANY:
            return
        seen = np.zeros(self.grid.shape, bool)
        same = self.grid == target
        stack = [(int(i), int(j))]
        seen[j, i] = True
        while stack:
            x, y = stack.pop()
            x0 = x
            while x0 > 0 and same[y, x0 - 1] and not seen[y, x0 - 1]:
                x0 -= 1
                seen[y, x0] = True
            x1 = x
            while x1 < self.W - 1 and same[y, x1 + 1] and not seen[y, x1 + 1]:
                x1 += 1
                seen[y, x1] = True
            for yy in (y - 1, y + 1):
                if 0 <= yy < self.H:
                    row = same[yy, x0:x1 + 1] & ~seen[yy, x0:x1 + 1]
                    for k in np.nonzero(row)[0]:
                        xx = x0 + int(k)
                        if not seen[yy, xx]:
                            seen[yy, xx] = True
                            stack.append((xx, yy))
        jj, ii = np.nonzero(seen)
        if self._put(jj, ii, code, weight):
            self.changed.emit("paint")

    def clear(self):
        self.begin()
        self.grid[:] = de.ANY
        self.weights[:] = 1.0
        self.commit("Clear")
        self.changed.emit("paint")

    def reset_weights(self):
        self.begin()
        self.weights[:] = 1.0
        self.commit("Reset weights")
        self.changed.emit("paint")

    def set_grid(self, grid, weights=None, label="Replace drawing"):
        g = np.asarray(grid, np.int32)
        self.begin()
        H, W = g.shape
        self.W, self.H = W, H
        self.grid = g.copy()
        self.weights = (np.ones((H, W), np.float32) if weights is None
                        else np.asarray(weights, np.float32).copy())
        self.commit(label)
        self.changed.emit("geometry")

    # transforms (whole drawing)
    def transform(self, kind):
        self.begin()
        f = {"flip_h": lambda a: a[:, ::-1], "flip_v": lambda a: a[::-1, :],
             "rot_cw": lambda a: np.rot90(a, -1), "rot_ccw": lambda a: np.rot90(a, 1)}[kind]
        self.grid = np.ascontiguousarray(f(self.grid))
        self.weights = np.ascontiguousarray(f(self.weights))
        self.H, self.W = self.grid.shape
        self.commit({"flip_h": "Flip horizontally", "flip_v": "Flip vertically",
                     "rot_cw": "Rotate right", "rot_ccw": "Rotate left"}[kind])
        self.changed.emit("geometry")

    def shift(self, di, dj):
        """Move the painting inside its grid (what leaves the grid is lost)."""
        self.begin()
        g = np.zeros_like(self.grid)
        w = np.ones_like(self.weights)
        H, W = self.H, self.W
        ys, yd = (slice(0, H - dj), slice(dj, H)) if dj >= 0 else (slice(-dj, H), slice(0, H + dj))
        xs, xd = (slice(0, W - di), slice(di, W)) if di >= 0 else (slice(-di, W), slice(0, W + di))
        g[yd, xd] = self.grid[ys, xs]
        w[yd, xd] = self.weights[ys, xs]
        self.grid, self.weights = g, w
        self.commit("Move drawing")
        self.changed.emit("paint")

    # ── geometry ────────────────────────────────────────────────────────────
    def set_size(self, W, H, keep="scale"):
        """New grid size. keep="scale": the painting is resampled (same shape,
        same world area when the cell size is adjusted by the caller);
        keep="crop": cells stay the same size, the canvas grows / shrinks
        around the centre."""
        W = max(MIN_SIDE, min(MAX_SIDE, int(W)))
        H = max(MIN_SIDE, min(MAX_SIDE, int(H)))
        if (W, H) == (self.W, self.H):
            return
        self.begin()
        if keep == "crop":
            g = np.zeros((H, W), np.int32)
            w = np.ones((H, W), np.float32)
            oy, ox = (H - self.H) // 2, (W - self.W) // 2
            sy0, sx0 = max(0, -oy), max(0, -ox)
            dy0, dx0 = max(0, oy), max(0, ox)
            h = min(self.H - sy0, H - dy0)
            ww = min(self.W - sx0, W - dx0)
            if h > 0 and ww > 0:
                g[dy0:dy0 + h, dx0:dx0 + ww] = self.grid[sy0:sy0 + h, sx0:sx0 + ww]
                w[dy0:dy0 + h, dx0:dx0 + ww] = self.weights[sy0:sy0 + h, sx0:sx0 + ww]
        else:
            if W < self.W or H < self.H:
                g = majority_downsample(self.grid, H, W)
                w = resample(self.weights, H, W, "mean")
                if g.shape != (H, W):
                    g = resample(g, H, W)
            else:
                g = resample(self.grid, H, W)
                w = resample(self.weights, H, W)
            w = np.asarray(w, np.float32)
            w[g == de.ANY] = 1.0
        self.W, self.H = W, H
        self.grid, self.weights = g.astype(np.int32), w.astype(np.float32)
        self.commit("Resize grid")
        self.changed.emit("geometry")

    def set_geometry(self, cell=None, center=None, slide=None, label="Placement"):
        new = (self.cell if cell is None else max(1, int(cell)),
               tuple(self.center) if center is None else (int(center[0]), int(center[1])),
               self.slide if slide is None else max(0, int(slide)))
        if new == (self.cell, tuple(self.center), self.slide):
            return
        self.begin()
        self.cell, self.center, self.slide = new
        self.commit(label)
        self.changed.emit("geometry")

    def set_balance(self, on):
        if bool(on) != self.balance:
            self.balance = bool(on)
            self.dirty = True
            self.changed.emit("meta")

    # ── world placement ─────────────────────────────────────────────────────
    def corner(self, ox=0, oz=0):
        return (self.center[0] - (self.W * self.cell) // 2 + ox * self.cell,
                self.center[1] - (self.H * self.cell) // 2 + oz * self.cell)

    def cell_at(self, wx, wz, ox=0, oz=0):
        x0, z0 = self.corner(ox, oz)
        return int(np.floor((wx - x0) / self.cell)), int(np.floor((wz - z0) / self.cell))

    def painted(self):
        return int(np.count_nonzero(self.grid))

    def used_codes(self):
        vals, cnt = np.unique(self.grid[self.grid != de.ANY], return_counts=True)
        return sorted(((int(v), int(c)) for v, c in zip(vals, cnt)), key=lambda t: -t[1])

    def spec(self, mc, large):
        return de.DrawSpec(self.grid.copy(), self.weights.copy(), cell=int(self.cell),
                           center=tuple(self.center), slide=int(self.slide), mc=int(mc),
                           large=bool(large), balance=self.balance)

    def signature(self):
        """What a search depends on (a paused search resumes only when it is
        unchanged)."""
        return (self.grid.tobytes(), self.weights.tobytes(), self.W, self.H, self.cell,
                tuple(self.center), self.slide, self.balance)

    # ── serialisation ───────────────────────────────────────────────────────
    def to_dict(self):
        return dict(format=FORMAT, version=VERSION, name=self.name, description=self.description,
                    created=self.created, modified=now_iso(), W=self.W, H=self.H,
                    cell=self.cell, center=list(self.center), slide=self.slide,
                    balance=self.balance, grid=_pack(self.grid.astype("<i4")),
                    weights=_pack(self.weights.astype("<f4")))

    def load_dict(self, d, path=None):
        if d.get("format") != FORMAT:
            raise ValueError("not an SSA drawing")
        W, H = int(d["W"]), int(d["H"])
        if not (MIN_SIDE <= W <= 512 and MIN_SIDE <= H <= 512):
            raise ValueError(f"invalid drawing size {W}×{H}")
        g = _unpack(d["grid"], "<i4", (H, W)).astype(np.int32)
        w = _unpack(d["weights"], "<f4", (H, W)).astype(np.float32)
        self.W, self.H, self.grid, self.weights = W, H, g, w
        self.cell = max(1, int(d.get("cell", 64)))
        c = d.get("center", (0, 0))
        self.center = (int(c[0]), int(c[1]))
        self.slide = max(0, int(d.get("slide", 0)))
        self.balance = bool(d.get("balance", False))
        self.name = str(d.get("name") or "Untitled drawing")
        self.description = str(d.get("description") or "")
        self.created = str(d.get("created") or now_iso())
        self.path = path
        self.dirty = False
        self._undo.clear(); self._redo.clear(); self._pending = None; self._depth = 0
        self.changed.emit("load")

    def new(self, W=48, H=48, cell=64):
        self.W, self.H = W, H
        self.grid = np.zeros((H, W), np.int32)
        self.weights = np.ones((H, W), np.float32)
        self.cell, self.center, self.slide, self.balance = cell, (0, 0), max(0, W // 4), False
        self.name, self.description, self.created = "Untitled drawing", "", now_iso()
        self.path = None
        self.dirty = False
        self._undo.clear(); self._redo.clear(); self._pending = None; self._depth = 0
        self.changed.emit("load")


# ── the classic UI's presets (drawing_presets.json) ─────────────────────────
_LEGACY_GROUPS = {500: 0, 501: 2, 503: 1}      # old group cid -> GROUPS index


def legacy_code(c: int) -> int:
    c = int(c)
    if c in (1, 2, 3, 4, 5):
        return c
    if c in _LEGACY_GROUPS:
        return de.group_code(_LEGACY_GROUPS[c])
    if c >= 1000:
        return c                                  # biome: same encoding
    return de.ANY


def from_legacy_preset(p: dict) -> dict:
    """Old preset → drawing dict (same world area, grid = its search
    resolution; the old slide was ¼ of the drawing)."""
    g = np.asarray(json.loads(p["grid"]) if isinstance(p["grid"], str) else p["grid"], np.int64)
    w = p.get("weights")
    w = (np.asarray(json.loads(w) if isinstance(w, str) else w, np.float32) if w is not None
         else np.ones(g.shape, np.float32))
    res = int(p.get("score_res") or g.shape[0])
    res = max(4, min(MAX_SIDE, res))
    area = int(p.get("area_blocks") or res * 64)
    g = np.vectorize(legacy_code, otypes=[np.int32])(g)
    g2 = majority_downsample(g.astype(np.int32), res, res)
    w2 = np.asarray(resample(w, res, res, "mean"), np.float32)
    w2[g2 == de.ANY] = 1.0
    cell = max(1, int(round(area / res)))
    m = DrawModel()
    m.W = m.H = res
    m.grid, m.weights = g2.astype(np.int32), w2
    m.cell, m.center = cell, (0, 0)
    m.slide = res // 4 if p.get("slide", True) else 0
    m.name = str(p.get("name") or "Preset")
    m.description = "Imported from the classic interface's presets."
    return m.to_dict()
