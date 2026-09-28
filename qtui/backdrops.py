"""
qtui/backdrops.py — procedural window backgrounds for the textured themes
(marble, aurora, nebula, stone, dunes…).

Each backdrop is computed once with numpy (≈0.2–0.5 s), saved as a PNG in the
temp folder (keyed by its parameters) and used by the stylesheet as the
window's border-image. Everything is deterministic: same theme, same picture.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time

import numpy as np

W, H = 1600, 1000
VERSION = 6
# sharp-edged backdrops (facets, leaves, petals) are rendered at a higher
# resolution: stretched over a large window, 1600 px looked blurry
HIRES = (2560, 1600)


# ── noise ─────────────────────────────────────────────────────────────────
def _value_noise(h, w, cell, rng, sx=1.0):
    """Smooth value noise, one octave (cell = feature size in pixels)."""
    cy, cx = max(1.0, cell), max(1.0, cell * sx)
    gh, gw = int(h / cy) + 3, int(w / cx) + 3
    g = rng.random((gh, gw)).astype(np.float32)
    y = np.arange(h, dtype=np.float32) / cy
    x = np.arange(w, dtype=np.float32) / cx
    y0 = y.astype(np.int32); x0 = x.astype(np.int32)
    ty = y - y0; tx = x - x0
    ty = ty * ty * (3 - 2 * ty); tx = tx * tx * (3 - 2 * tx)
    a = g[y0][:, x0]; b = g[y0][:, x0 + 1]
    c = g[y0 + 1][:, x0]; d = g[y0 + 1][:, x0 + 1]
    top = a + (b - a) * tx[None, :]
    bot = c + (d - c) * tx[None, :]
    return top + (bot - top) * ty[:, None]


def _fbm(h, w, cell, octaves, rng, gain=0.5, sx=1.0):
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for _ in range(octaves):
        out += amp * _value_noise(h, w, cell, rng, sx)
        tot += amp
        amp *= gain
        cell /= 2.0
        if cell < 1.5:
            break
    return out / tot


def _rgb(hex_):
    hex_ = hex_.lstrip("#")
    return np.array([int(hex_[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def _mix(a, b, t):
    t = np.clip(t, 0, 1)[..., None]
    return a * (1 - t) + b * t


def _grad(stops, t):
    """Multi-stop colour ramp: stops = [(pos, '#hex'), …]."""
    t = np.clip(t, 0, 1)
    out = np.zeros(t.shape + (3,), np.float32)
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        m = (t >= p0) & (t <= p1)
        if not m.any():
            continue
        u = (t[m] - p0) / max(1e-6, p1 - p0)
        out[m] = _rgb(c0) * (1 - u[:, None]) + _rgb(c1) * u[:, None]
    return out


def _yx(h, w):
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    return y / h, x / w


def _vignette(img, strength=0.35):
    y, x = _yx(*img.shape[:2])
    d = np.sqrt((x - 0.5) ** 2 * 1.2 + (y - 0.45) ** 2)
    return img * (1 - strength * np.clip(d * 1.4, 0, 1) ** 2)[..., None]


def _grain(img, rng, amt=4.0):
    return img + (rng.random(img.shape[:2]).astype(np.float32)[..., None] - 0.5) * amt


def _stars(img, rng, n=900, color="#ffffff", big=0.04):
    h, w = img.shape[:2]
    ys = rng.integers(0, h, n); xs = rng.integers(0, w, n)
    br = rng.random(n) ** 3
    c = _rgb(color)
    img[ys, xs] = img[ys, xs] * (1 - br[:, None]) + c * br[:, None]
    for i in np.nonzero(rng.random(n) < big)[0]:            # a few soft bright ones
        y, x = ys[i], xs[i]
        y0, y1, x0, x1 = max(0, y - 3), min(h, y + 4), max(0, x - 3), min(w, x + 4)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        f = np.exp(-((yy - y) ** 2 + (xx - x) ** 2) / 2.5)[..., None] * 0.8
        img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - f) + c * f
    return img


# ── generators ────────────────────────────────────────────────────────────
def marble(rng, base=("#0b0a09", "#1b1915"), veins=("#c9a13b", "#f1d68c"), vein_amt=0.9,
           secondary="#6e6a62", scale=1.0, angle=0.55, light=False):
    """Domain-warped marble: long flowing veins (not contour lines), each
    fading in and out, finer secondary veins, cloudy base."""
    h, w = H, W
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    wx = _fbm(h, w, 520 * scale, 4, rng, 0.45) - 0.5          # smooth domain warp
    wy = _fbm(h, w, 520 * scale, 4, rng, 0.45) - 0.5
    xs = x + wx * 420 * scale
    ys = y + wy * 420 * scale
    u = (xs * np.cos(angle) + ys * np.sin(angle)) / (260 * scale)
    t1 = _fbm(h, w, 300 * scale, 4, rng, 0.42)
    v1 = np.abs(np.sin(u * np.pi + t1 * 4.5))
    v2 = np.abs(np.sin(u * np.pi * 3.1 + _fbm(h, w, 180 * scale, 4, rng, 0.45) * 6.0 + 0.7))
    life1 = np.clip((_fbm(h, w, 380 * scale, 3, rng) - 0.32) * 2.6, 0, 1)   # veins fade out
    life2 = np.clip((_fbm(h, w, 260 * scale, 3, rng) - 0.45) * 3.0, 0, 1)
    main = (1 - v1) ** 30 * life1
    halo = (1 - v1) ** 7 * 0.28 * life1
    fine = (1 - v2) ** 70 * life2
    cloud = _fbm(h, w, 420 * scale, 5, rng, 0.5)
    img = _mix(_rgb(base[0]), _rgb(base[1]), (cloud - 0.3) * 2.0)
    img = _mix(img, _rgb(secondary), fine * 0.6)
    vcol = _mix(_rgb(veins[0]), _rgb(veins[1]), _fbm(h, w, 140, 3, rng) * 1.2 - 0.1)
    a = np.clip((main + halo) * vein_amt, 0, 1)[..., None]
    img = img * (1 - a) + vcol * a
    img = _grain(img, rng, 4)
    return img if light else _vignette(img, 0.3)


def aurora(rng, sky=("#02040a", "#081a33"), curtains=("#3dffb0", "#35c7ff", "#9b6bff")):
    h, w = H, W
    yy, xx = _yx(h, w)
    img = _mix(_rgb(sky[1]), _rgb(sky[0]), yy * 1.15)
    img = _stars(img, rng, 1300, "#dfe9ff")
    glow_all = np.zeros((h, w), np.float32)
    for i, col in enumerate(curtains):
        phase = rng.random() * 6.28
        centre = 0.20 + 0.09 * i + 0.05 * np.sin(xx[0] * 6.28 * (0.9 + 0.4 * i) + phase) \
            + 0.05 * (_fbm(1, w, 300, 4, rng)[0] - 0.5)
        rays = _fbm(1, w, 9, 3, rng, 0.6)[0]
        rays = 0.25 + 0.75 * np.clip((rays - 0.35) * 2.2, 0, 1)
        fade_x = np.clip(_fbm(1, w, 420, 3, rng)[0] * 2.2 - 0.5, 0, 1)    # curtains come and go
        d = yy - centre[None, :]
        prof = np.where(d > 0, np.exp(-(d / 0.012) ** 2), np.exp(-(d / (0.11 - 0.02 * i)) ** 2))
        inten = prof * (rays * fade_x)[None, :] * (0.8 - 0.2 * i)
        glow_all += inten
        img = img + _rgb(col) * inten[..., None] * 0.85
    # faint light on the ground below the curtains
    img = img + _rgb(curtains[0]) * (np.clip(glow_all.mean(axis=0), 0, 1)[None, :] *
                                     np.exp(-((yy - 0.95) / 0.25) ** 2) * 0.08)[..., None]
    img = _grain(img, rng, 3)
    return _vignette(img, 0.25)


def nebula(rng, deep="#05030d", clouds=("#5b2a9e", "#c03c8a", "#2b5fd9"), star="#ffffff",
           density=1.0):
    h, w = H, W
    img = np.zeros((h, w, 3), np.float32) + _rgb(deep)
    for col in clouds:
        f = _fbm(h, w, 520, 7, rng, 0.55)
        m = np.clip((f - 0.48) * 3.2 * density, 0, 1) ** 1.6
        img = img + _rgb(col) * m[..., None] * 0.55
    dust = _fbm(h, w, 200, 6, rng, 0.6)
    img = img * (0.65 + 0.5 * dust[..., None])
    img = _stars(img, rng, 2600, star, 0.05)
    img = _grain(img, rng, 3)
    return _vignette(img, 0.3)


def stone(rng, dark="#1c1d21", light="#34363c", specks=("#15161a", "#4a4d55"), cell=90,
          accent_specks=None, crack=None):
    """Deepslate / netherrack / end stone like surfaces."""
    h, w = H, W
    f = _fbm(h, w, cell, 6, rng, 0.62)
    img = _mix(_rgb(dark), _rgb(light), (f - 0.35) * 2.2)
    s = rng.random((h, w)).astype(np.float32)
    img[s < 0.02] = _mix(img[s < 0.02], _rgb(specks[0]), np.full((int((s < 0.02).sum()),), 0.6))
    img[s > 0.985] = _mix(img[s > 0.985], _rgb(specks[1]), np.full((int((s > 0.985).sum()),), 0.5))
    if crack:
        c = _fbm(h, w, 160, 5, rng, 0.5)
        lines = np.exp(-((c - 0.5) / 0.012) ** 2)
        img = _mix(img, _rgb(crack), lines * 0.55)
    if accent_specks:
        g = _fbm(h, w, 40, 3, rng)
        m = (g > 0.72) & (rng.random((h, w)) < 0.35)
        img[m] = _mix(img[m], _rgb(accent_specks), np.full((int(m.sum()),), 0.65))
    img = _grain(img, rng, 7)
    return _vignette(img, 0.35)


def glow(rng, base=("#140c08", "#23140c"), blobs=(("#ff7a2e", 0.25, 0.15, 0.45),),
         grain=4, light=False, ridges=None, petals=None, leaves=None):
    """Smooth gradient with soft glowing blobs (sunset, sakura, copper…)."""
    h, w = H, W
    yy, xx = _yx(h, w)
    img = _mix(_rgb(base[0]), _rgb(base[1]), yy * 0.8 + xx * 0.2)
    for col, bx, by, r in blobs:
        d = np.sqrt((xx - bx) ** 2 * (w / h) ** 2 + (yy - by) ** 2)
        img = img + _rgb(col) * (np.exp(-(d / r) ** 2) * (0.6 if not light else 0.35))[..., None]
    if petals:                                      # soft falling petals
        col, n = petals
        c = _rgb(col)
        for _ in range(int(n)):
            cy, cx = rng.random() * h, rng.random() * w
            a = rng.random() * np.pi
            kk = W / 1600.0
            rx, ry = (7 + rng.random() * 9) * kk, (4 + rng.random() * 5) * kk
            R = int(max(rx, ry)) + 2
            y0, y1, x0, x1 = int(max(0, cy - R)), int(min(h, cy + R)), int(max(0, cx - R)), int(min(w, cx + R))
            if y1 <= y0 or x1 <= x0:
                continue
            py, px = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            dy, dx = py - cy, px - cx
            u = dx * np.cos(a) + dy * np.sin(a); v = -dx * np.sin(a) + dy * np.cos(a)
            m = np.clip(1.4 - np.sqrt((u / rx) ** 2 + (v / ry) ** 2), 0, 1) * (0.35 + 0.4 * rng.random())
            img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - m[..., None]) + c * m[..., None]
    if leaves:                                      # crisp leaves (pointed, midrib, shades)
        cols, n = leaves
        cs_ = [_rgb(c) for c in cols]
        kk = W / 1600.0
        for _ in range(int(n)):
            cy, cx = rng.random() * h, rng.random() * w
            a = rng.random() * np.pi
            L = (14 + rng.random() * 22) * kk; Wd = L * (0.28 + rng.random() * 0.12)
            R = int(L) + 3
            y0, y1, x0, x1 = int(max(0, cy - R)), int(min(h, cy + R)), int(max(0, cx - R)), int(min(w, cx + R))
            if y1 <= y0 or x1 <= x0:
                continue
            py, px = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            dy, dx = py - cy, px - cx
            u = (dx * np.cos(a) + dy * np.sin(a)) / L
            v = (-dx * np.sin(a) + dy * np.cos(a)) / Wd
            half = np.clip(1 - u * u, 0, 1) ** 0.8           # pointed both ends
            dist = np.abs(v) - half
            m = np.clip(-dist * Wd * 1.2, 0, 1)               # ~1 px anti-aliased edge
            depth = 0.55 + 0.45 * rng.random()                # near / far leaves
            c = cs_[int(rng.integers(len(cs_)))] * depth
            vein = np.clip(1 - np.abs(v) * Wd / 0.8, 0, 1) * (np.abs(u) < 0.9) * 0.35
            leaf = c * (1 - vein[..., None]) + (c * 1.35) * vein[..., None]
            img[y0:y1, x0:x1] = img[y0:y1, x0:x1] * (1 - m[..., None]) + leaf * m[..., None]
    if ridges:                                      # dune crests / caustics
        col, amt, cell = ridges
        f = _fbm(h, w, cell, 5, rng, 0.5)
        r = np.abs(np.sin(f * 40.0 + yy * 18.0))
        img = _mix(img, _rgb(col), (1 - r) ** 12 * amt)
    img = _grain(img, rng, grain)
    return img if light else _vignette(img, 0.3)


def brushed(rng, base=("#1b1d21", "#2b2e34"), sheen="#8a929e", tint=None):
    h, w = H, W
    yy, xx = _yx(h, w)
    streaks = _fbm(h, w, 3.0, 3, rng, 0.6, sx=90.0)
    img = _mix(_rgb(base[0]), _rgb(base[1]), 0.35 + (streaks - 0.5) * 1.4)
    band = np.exp(-((yy - 0.25 - 0.25 * xx) / 0.28) ** 2)
    img = img + _rgb(sheen) * (band * 0.12)[..., None]
    if tint:                                        # patina patches with crisp edges
        col, amt = tint
        f = _fbm(h, w, 200, 6, rng, 0.6)
        m = np.clip((f - 0.58) * 7.0, 0, 1) * amt
        pat = _mix(_rgb(col), _rgb(col) * 1.35, _fbm(h, w, 25, 3, rng))
        img = img * (1 - m[..., None]) + pat * m[..., None]
    img = _grain(img, rng, 4)
    return _vignette(img, 0.3)


def strata(rng, bands=("#8a3f24", "#b2582e", "#d4834a", "#c46a3a", "#e6b98a", "#9a4a2c",
                        "#7a3524", "#caa27a"), warp=38.0, light=False):
    """Layered strata (badlands terracotta): wavy horizontal bands of uneven
    thickness, grain, a soft sun."""
    h, w = H, W
    yy, xx = _yx(h, w)
    wv = (_fbm(h, w, 420, 4, rng, 0.5) - 0.5) * warp / h
    t = yy + wv + 0.03 * np.sin(xx * 5.0 + rng.random() * 6.28)
    edges = np.cumsum(0.4 + rng.random(22) ** 2 * 2.0)   # few bands, uneven thickness
    edges = edges / edges[-1]
    idx = np.minimum(np.searchsorted(edges, np.clip(t * 0.8 + 0.1, 0, 0.9999)), 21)
    cols = np.array([_rgb(bands[int(rng.integers(len(bands)))]) for i in range(22)], np.float32)
    img = cols[idx]
    img = img * (0.88 + 0.24 * _fbm(h, w, 60, 4, rng, 0.55)[..., None])
    img = _grain(img, rng, 6)
    return img if light else _vignette(img, 0.32)


def crystal(rng, dark="#1a0f2e", light="#5b3a8f", edge="#d7b8ff", n=90, glint="#ffffff"):
    """Geode / crystal facets: full-resolution Voronoi cells, each lit by its
    own light direction (a flat gradient = a cut face), crisp anti-aliased
    bright edges, a few glints (amethyst, prismarine)."""
    h, w = H, W
    k = w / 1600.0                                   # feature scale (hi-res render)
    py = (rng.random(n) * h).astype(np.float32); px = (rng.random(n) * w).astype(np.float32)
    base = rng.random(n).astype(np.float32)
    ang = rng.random(n).astype(np.float32) * 6.2832
    gx, gz = np.cos(ang), np.sin(ang)
    img = np.empty((h, w, 3), np.float32)
    cd, cl, ce = _rgb(dark), _rgb(light), _rgb(edge)
    xs = np.arange(w, dtype=np.float32)
    for y0 in range(0, h, 64):                        # rows in blocks: bounded memory
        ys = np.arange(y0, min(h, y0 + 64), dtype=np.float32)
        dy = ys[:, None, None] - py[None, None, :]
        dx = xs[None, :, None] - px[None, None, :]
        d = dx * dx + dy * dy                         # (rows, w, n)
        two = np.argpartition(d, 1, axis=2)[..., :2]
        da = np.take_along_axis(d, two, 2)
        swap = da[..., 0] > da[..., 1]
        i0 = np.where(swap, two[..., 1], two[..., 0])
        d0 = np.sqrt(np.minimum(da[..., 0], da[..., 1]))
        d1 = np.sqrt(np.maximum(da[..., 0], da[..., 1]))
        rx = xs[None, :] - px[i0]; ry = ys[:, None] - py[i0]
        face = base[i0] * 0.65 + 0.35 * (0.5 + 0.5 * np.tanh((rx * gx[i0] + ry * gz[i0]) / (160 * k)))
        col = _mix(cd, cl, np.clip(face, 0, 1))
        e = np.clip(1.0 - (d1 - d0) / (2.2 * k), 0, 1) ** 1.5   # anti-aliased edge
        img[y0:y0 + len(ys)] = _mix(col, ce, e * 0.6)
    img = _stars(img, rng, int(220 * k * k), glint, 0.12)
    img = _grain(img, rng, 3)
    return _vignette(img, 0.35)


GENERATORS = dict(marble=marble, aurora=aurora, nebula=nebula, stone=stone, glow=glow,
                  brushed=brushed, strata=strata, crystal=crystal)


def _key(spec):
    return hashlib.md5(json.dumps([VERSION, spec], sort_keys=True).encode()).hexdigest()[:12]


def render(spec) -> np.ndarray:
    kind = spec["kind"]
    kw = {k: (tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list) else v)
          for k, v in spec.items() if k not in ("kind", "seed")}
    rng = np.random.default_rng(int(spec.get("seed", 7)))
    global W, H
    hires = kind == "crystal" or bool(spec.get("petals")) or bool(spec.get("leaves"))
    saved = (W, H)
    if hires:
        W, H = HIRES
    try:
        img = GENERATORS[kind](rng, **kw)
    finally:
        W, H = saved
    return np.clip(img, 0, 255).astype(np.uint8)


def backdrop_path(spec) -> str | None:
    """PNG of the backdrop (computed once, cached in the temp folder)."""
    if not spec:
        return None
    d = os.path.join(tempfile.gettempdir(), "ssa_qt_assets", "backdrops")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{spec['kind']}_{_key(spec)}.png")
    if not os.path.exists(path):
        try:
            from PySide6.QtGui import QImage
            arr = np.ascontiguousarray(render(spec))
            h, w = arr.shape[:2]
            img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            # unique temp name + tolerant replace: two threads (or two SSA
            # windows) can render the same backdrop; on Windows os.replace fails
            # while the other one holds the file (WinError 32) — the first copy
            # written is as good as ours.
            tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp.png"
            img.save(tmp, "PNG")
            for _k in range(10):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    if os.path.exists(path):
                        break
                    time.sleep(0.05)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        except Exception:
            import traceback
            traceback.print_exc()
            return None
    return path.replace("\\", "/")
