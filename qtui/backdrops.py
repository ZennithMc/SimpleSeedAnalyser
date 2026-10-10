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
VERSION = 7
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
         grain=4, light=False, ridges=None, petals=None, leaves=None, crisp=False, stars=0):
    """Smooth gradient with soft glowing blobs (sunset, sakura, copper…)."""
    h, w = H, W
    yy, xx = _yx(h, w)
    img = _mix(_rgb(base[0]), _rgb(base[1]), yy * 0.8 + xx * 0.2)
    for col, bx, by, r in blobs:
        d = np.sqrt((xx - bx) ** 2 * (w / h) ** 2 + (yy - by) ** 2)
        img = img + _rgb(col) * (np.exp(-(d / r) ** 2) * (0.6 if not light else 0.35))[..., None]
    if stars:                                       # crisp stars (dusk / night skies)
        img = _stars(img, rng, int(stars * (W / 1600.0) ** 2), "#fff4ff", 0.06)
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
            rr = np.sqrt((u / rx) ** 2 + (v / ry) ** 2)
            if crisp:                               # sharp petal, ~1 px edge, a lighter tip
                m = np.clip((1.0 - rr) * min(rx, ry) * 1.2, 0, 1) * (0.55 + 0.4 * rng.random())
            else:
                m = np.clip(1.4 - rr, 0, 1) * (0.35 + 0.4 * rng.random())
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


def planks(rng, base=("#6b4a2b", "#9b7346"), seam="#2a1a0e", rows=(70, 125), length=(380, 980),
           grain=1.0, knots=7, light=False):
    """Wooden planks (08/10): staggered boards of uneven length, each its own
    tone, with long grain, growth rings bending around a few knots, dark seams
    and a soft light from the top left."""
    h, w = H, W
    yy, xx = _yx(h, w)
    py = np.arange(h, dtype=np.float32)
    px = np.arange(w, dtype=np.float32)
    # rows of boards
    edges = [0]
    while edges[-1] < h:
        edges.append(edges[-1] + int(rng.integers(rows[0], rows[1])))
    row = np.searchsorted(np.array(edges[1:]), py, side="right")          # (h,)
    nrow = len(edges)
    # boards along each row (staggered joints)
    board = np.zeros((h, w), np.int32)
    seam_m = np.zeros((h, w), np.float32)
    bid = 0
    tones = []
    for r in range(nrow):
        y0, y1 = edges[r], min(h, edges[r + 1] if r + 1 < len(edges) else h)
        if y0 >= h:
            break
        x = -int(rng.integers(0, length[1]))
        while x < w:
            L = int(rng.integers(length[0], length[1]))
            a, b = max(0, x), min(w, x + L)
            if b > a:
                board[y0:y1, a:b] = bid
                seam_m[y0:y1, max(0, b - 2):b] = 1.0             # end joint
                tones.append(rng.random())
                bid += 1
            x += L
        seam_m[max(0, y1 - 2):y1, :] = 1.0                       # long seam
    tones = np.array(tones + [0.5], np.float32)
    t = tones[board]
    # grain: long streaks + growth rings warped around knots
    streak = _fbm(h, w, 3.0, 3, rng, 0.6, sx=60.0)
    warp = (_fbm(h, w, 160, 3, rng, 0.5) - 0.5) * 40.0
    yk = py[:, None] + warp + board.astype(np.float32) * 37.0
    for _k in range(int(knots)):
        kx, ky = rng.random() * w, rng.random() * h
        rx, ry = 40 + rng.random() * 70, 10 + rng.random() * 16
        d = np.sqrt(((px[None, :] - kx) / rx) ** 2 + ((py[:, None] - ky) / ry) ** 2)
        yk = yk + 26.0 * np.exp(-d * d) * np.sign(py[:, None] - ky + 0.01)
    rings = 0.5 + 0.5 * np.sin(yk * (0.42 * grain) + streak * 5.0)
    shade = 0.32 + t * 0.5 + (rings - 0.5) * 0.22 * grain + (streak - 0.5) * 0.35
    img = _mix(_rgb(base[0]), _rgb(base[1]), shade)
    sm = np.clip(seam_m, 0, 1)[..., None]
    img = img * (1 - sm) + _rgb(seam) * sm
    # light from the top left, a slight bevel under each seam
    img = img * (0.92 + 0.14 * (1 - yy) * (1 - 0.5 * xx))[..., None]
    img = _grain(img, rng, 3 if light else 5)
    return _vignette(img, 0.18 if light else 0.32)


def synthwave(rng, sky=("#0b0420", "#3a0a4a", "#ff3d8a"), sun=("#ffd34d", "#ff3d8a"),
              grid="#38e8ff", ground="#090216", horizon=0.56):
    """(10/10) Retro 80s: gradient sky, striped setting sun, perspective neon
    grid on a dark plane."""
    h, w = H, W
    yy, xx = _yx(h, w)
    img = _grad([(0.0, sky[0]), (0.6, sky[1]), (1.0, sky[2])], yy / horizon)
    img = _stars(img, rng, 500, "#ffe6ff", 0.03)
    # sun: disc with horizontal cuts towards its base
    cx, cy, r = 0.5, horizon - 0.05, 0.19
    d = np.sqrt(((xx - cx) * w / h) ** 2 + (yy - cy) ** 2)
    disc = np.clip((r - d) * h / 1.5, 0, 1)
    u = (yy - (cy - r)) / (2 * r)
    cuts = np.where(u > 0.3, (np.sin((u - 0.3) * 70.0) > -0.75 + 2.4 * (u - 0.3)), 1.0)
    scol = _mix(_rgb(sun[0]), _rgb(sun[1]), u * 1.1)
    m = (disc * cuts * (yy < horizon))[..., None]
    img = img * (1 - m) + scol * m
    halo = np.exp(-(d / (r * 1.8)) ** 2) * 0.35 * (yy < horizon)
    img = img + _rgb(sun[1]) * halo[..., None]
    # ground + perspective grid
    gm = yy >= horizon
    g = np.clip((yy - horizon) / (1 - horizon), 1e-3, 1)
    img[gm] = _rgb(ground)
    zc = 1.0 / g                                        # depth
    xw = (xx - 0.5) * zc * 8.0
    lx = np.abs(xw - np.round(xw))
    lz = np.abs(zc * 2.0 - np.round(zc * 2.0))
    aa = 0.9 / (h * (1 - horizon)) * zc * 2.0           # line width in depth units
    line = np.maximum(np.clip(1 - lx / (0.03 * zc ** 0.5 + 1e-3), 0, 1),
                      np.clip(1 - lz / np.maximum(aa * 1.5, 0.02), 0, 1))
    fade = np.clip(g * 2.2, 0, 1)
    gl = (line * fade * gm)[..., None]
    img = img * (1 - gl * 0.85) + _rgb(grid) * gl * 0.85
    img = img + _rgb(grid) * (np.exp(-((yy - horizon) / 0.012) ** 2) * 0.5)[..., None]
    img = _grain(img, rng, 3)
    return _vignette(img, 0.3)


def blueprint(rng, base=("#0d3a73", "#0a2c58"), line="#cfe3ff", cell=40, major=5):
    """(10/10) Blueprint paper: fine and major grid lines, faint drawings."""
    h, w = H, W
    yy, xx = _yx(h, w)
    img = _mix(_rgb(base[0]), _rgb(base[1]), yy * 0.7 + 0.3 * _fbm(h, w, 400, 4, rng))
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    fx, fy = np.abs((x / cell) - np.round(x / cell)) * cell, np.abs((y / cell) - np.round(y / cell)) * cell
    Mx = np.abs((x / (cell * major)) - np.round(x / (cell * major))) * cell * major
    My = np.abs((y / (cell * major)) - np.round(y / (cell * major))) * cell * major
    fine = np.maximum(np.clip(1 - fx, 0, 1), np.clip(1 - fy, 0, 1)) * 0.16
    big = np.maximum(np.clip(1.5 - Mx, 0, 1), np.clip(1.5 - My, 0, 1)) * 0.32
    img = _mix(img, _rgb(line), np.maximum(fine, big))
    # a few faint technical shapes: circles and rectangles
    for _ in range(9):
        cxp, cyp = rng.random() * w, rng.random() * h
        R = 40 + rng.random() * 160
        dd = np.abs(np.sqrt((x - cxp) ** 2 + (y - cyp) ** 2) - R)
        img = _mix(img, _rgb(line), np.clip(1.3 - dd, 0, 1) * 0.30)
    for _ in range(6):
        x0, y0 = rng.random() * w, rng.random() * h
        ww, hh = 80 + rng.random() * 300, 50 + rng.random() * 200
        edge = ((np.abs(x - x0) < 1.0) | (np.abs(x - x0 - ww) < 1.0)) & (y > y0) & (y < y0 + hh)
        edge |= ((np.abs(y - y0) < 1.0) | (np.abs(y - y0 - hh) < 1.0)) & (x > x0) & (x < x0 + ww)
        img = _mix(img, _rgb(line), edge.astype(np.float32) * 0.28)
    img = _grain(img, rng, 3)
    return _vignette(img, 0.25)


def paper(rng, base=("#f3ead6", "#e4d6b8"), fiber="#c9b48a", stain="#b89a64", light=True):
    """(10/10) Old parchment: warm tone, fibres, soft stains, darker edges."""
    h, w = H, W
    yy, xx = _yx(h, w)
    cloud = _fbm(h, w, 300, 6, rng, 0.55)
    img = _mix(_rgb(base[0]), _rgb(base[1]), (cloud - 0.35) * 1.6)
    fib = _fbm(h, w, 2.0, 3, rng, 0.6, sx=25.0)
    img = _mix(img, _rgb(fiber), np.clip((fib - 0.7) * 2.5, 0, 1) * 0.18)
    st = _fbm(h, w, 220, 5, rng, 0.5)
    img = _mix(img, _rgb(stain), np.clip((st - 0.66) * 4.0, 0, 1) * 0.35)
    d = np.minimum(np.minimum(xx, 1 - xx), np.minimum(yy, 1 - yy))
    img = img * (0.86 + 0.14 * np.clip(d * 9, 0, 1))[..., None]
    return _grain(img, rng, 5)


def circuit(rng, base=("#03140b", "#062417"), trace="#2bd67b", pad="#a6ffcf", n=70, step=24):
    """(10/10) Circuit board: right-angle traces on a grid, vias and pads."""
    h, w = H, W
    yy, xx = _yx(h, w)
    img = _mix(_rgb(base[0]), _rgb(base[1]), 0.4 + 0.6 * _fbm(h, w, 380, 4, rng))
    m = np.zeros((h, w), np.float32)
    pads = np.zeros((h, w), np.float32)
    gw, gh = w // step, h // step
    for _ in range(int(n)):
        gx, gy = int(rng.integers(1, gw - 1)), int(rng.integers(1, gh - 1))
        seg = int(rng.integers(3, 9))
        pts = [(gx, gy)]
        dirn = int(rng.integers(4))
        for _s in range(seg):
            if rng.random() < 0.45:
                dirn = (dirn + (1 if rng.random() < 0.5 else 3)) % 4
            L = int(rng.integers(2, 9))
            dx, dy = [(1, 0), (0, 1), (-1, 0), (0, -1)][dirn]
            nx = min(max(1, pts[-1][0] + dx * L), gw - 2); ny = min(max(1, pts[-1][1] + dy * L), gh - 2)
            pts.append((nx, ny))
        th = 1 + int(rng.integers(0, 3))
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            x0, x1 = sorted((ax * step, bx * step)); y0, y1 = sorted((ay * step, by * step))
            m[max(0, y0 - th):y1 + th + 1, max(0, x0 - th):x1 + th + 1] = 1.0
        for (px_, py_) in (pts[0], pts[-1]):
            cxp, cyp = px_ * step, py_ * step
            y0, y1, x0, x1 = max(0, cyp - 8), min(h, cyp + 9), max(0, cxp - 8), min(w, cxp + 9)
            yy2, xx2 = np.mgrid[y0:y1, x0:x1]
            r = np.sqrt((yy2 - cyp) ** 2 + (xx2 - cxp) ** 2)
            pads[y0:y1, x0:x1] = np.maximum(pads[y0:y1, x0:x1], np.clip(6.5 - r, 0, 1) * (r > 2.5))
    img = _mix(img, _rgb(trace) * 0.55, m * 0.75)
    img = _mix(img, _rgb(pad), pads * 0.85)
    img = _grain(img, rng, 4)
    return _vignette(img, 0.32)


def hexagons(rng, dark="#2a1702", light="#8a5410", edge="#ffc94a", size=46, fill=0.75):
    """(10/10) Honeycomb: hexagonal cells, each its own amber tone, bright
    rims, some cells empty (darker)."""
    h, w = H, W
    k = w / 1600.0                                   # hi-res render: same pattern, sharper
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    s = float(size) * k
    # axial hex coordinates (pointy top)
    q = (np.sqrt(3) / 3 * x - y / 3) / s
    r = (2.0 / 3 * y) / s
    cx_, cz_ = q, r
    cy_ = -cx_ - cz_
    rx, ry, rz = np.round(cx_), np.round(cy_), np.round(cz_)
    dx, dy, dz = np.abs(rx - cx_), np.abs(ry - cy_), np.abs(rz - cz_)
    a = (dx > dy) & (dx > dz); b = ~a & (dy > dz)
    rx0, ry0, rz0 = rx, ry, rz                       # cube rounding (from the rounded values)
    rx = np.where(a, -ry0 - rz0, rx0)
    ry = np.where(b, -rx0 - rz0, ry0)
    rz = np.where(~a & ~b, -rx0 - ry0, rz0)
    # distance to the centre, 0.5 on every edge of the cell (the edges bisect
    # the neighbours, which differ by +1/-1 on two cube coordinates)
    dq, dr, ds = cx_ - rx, cy_ - ry, cz_ - rz
    e = np.maximum(np.maximum(np.abs(dq - dr), np.abs(dr - ds)), np.abs(ds - dq)) / 2.0
    hid = ((rx.astype(np.int64) * 73856093) ^ (rz.astype(np.int64) * 19349663)) & 1023
    tone = (hid.astype(np.float32) / 1023.0)
    full = (tone < fill).astype(np.float32)
    shade = 0.35 + 0.65 * tone / max(fill, 1e-3)
    inner = np.clip(1 - e * 2.0, 0, 1)
    col = _mix(_rgb(dark), _rgb(light), shade * full * (0.6 + 0.4 * inner))
    # crisp rim: ~1 px anti-aliased edges (e is in cell units, 1 px = 1/s)
    rim = np.clip((e - 0.455) * s * 1.3, 0, 1)
    img = _mix(col, _rgb(edge) * 0.7, rim * 0.8)
    img = _grain(img, rng, 4)
    return _vignette(img, 0.35)


GENERATORS = dict(marble=marble, aurora=aurora, nebula=nebula, stone=stone, glow=glow,
                  brushed=brushed, strata=strata, crystal=crystal, planks=planks,
                  synthwave=synthwave, blueprint=blueprint, paper=paper, circuit=circuit,
                  hexagons=hexagons)


def _key(spec):
    return hashlib.md5(json.dumps([VERSION, spec], sort_keys=True).encode()).hexdigest()[:12]


def render(spec) -> np.ndarray:
    veil = spec.get("veil")
    if veil:
        # (08/10) the backdrop under a uniform veil of the panel colour: the
        # whole window is evened out instead of dark bands with holes
        base = render({k: v for k, v in spec.items() if k != "veil"}).astype(np.float32)
        col = _rgb(veil[0]); a = float(veil[1])
        return np.clip(base * (1.0 - a) + col[None, None, :] * a, 0, 255).astype(np.uint8)
    kind = spec["kind"]
    kw = {k: (tuple(tuple(x) if isinstance(x, list) else x for x in v) if isinstance(v, list) else v)
          for k, v in spec.items() if k not in ("kind", "seed")}
    rng = np.random.default_rng(int(spec.get("seed", 7)))
    global W, H
    hires = kind in ("crystal", "hexagons") or bool(spec.get("petals")) or bool(spec.get("leaves"))
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
