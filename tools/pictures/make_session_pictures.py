"""
tools/pictures/make_session_pictures.py — (19/11) the gallery of pictures a library
session can take (Sessions → Library → its picture → « Choose from the gallery »).

Every picture is original pixel art drawn here from code (noise, palettes, small
sprites): no game texture, no screenshot. 64 × 64 pixels, saved ×4 (256 px,
nearest neighbour) in assets/session_pictures/<key>.png, with index.json
(key, title, category, tags) for the gallery's search.

    py tools/pictures/make_session_pictures.py            # all of them
    py tools/pictures/make_session_pictures.py sheet      # + a contact sheet (_sheet.png)
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
from PIL import Image

N = 64
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "assets", "session_pictures")
BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16.0
SCENES = []


def scene(key, title, cat, tags=""):
    def deco(fn):
        SCENES.append((key, title, cat, tags, fn))
        return fn
    return deco


# ── colours & noise ──────────────────────────────────────────────────────────
def C(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def mix(a, b, t):
    return a + (b - a) * t


def vnoise(n, scale, seed):
    """n × n value noise in [0, 1], cells of `scale` pixels (smoothstep)."""
    r = np.random.default_rng(seed)
    m = int(n / scale) + 3
    g = r.random((m, m))
    c = np.arange(n) / scale
    i = c.astype(int)
    f = c - i
    f = f * f * (3 - 2 * f)
    a, b = g[np.ix_(i, i)], g[np.ix_(i, i + 1)]
    cc, d = g[np.ix_(i + 1, i)], g[np.ix_(i + 1, i + 1)]
    fy, fx = f[:, None], f[None, :]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (cc * (1 - fx) + d * fx) * fy


def fbm(n, scale, seed, oct=4, gain=0.5):
    out, amp, tot = np.zeros((n, n)), 1.0, 0.0
    for o in range(oct):
        out += amp * vnoise(n, max(1.0, scale / 2 ** o), seed * 31 + o)
        tot += amp
        amp *= gain
    return out / tot


def noise1(n, scale, seed, oct=3):
    r = np.random.default_rng(seed)
    out, amp, tot = np.zeros(n), 1.0, 0.0
    for o in range(oct):
        s = max(1.0, scale / 2 ** o)
        g = r.random(int(n / s) + 3)
        c = np.arange(n) / s
        i = c.astype(int)
        f = c - i
        f = f * f * (3 - 2 * f)
        out += amp * (g[i] * (1 - f) + g[i + 1] * f)
        tot += amp
        amp *= 0.5
    return out / tot


def grid():
    y, x = np.mgrid[0:N, 0:N].astype(float)
    return x, y


def radial(cx=N / 2, cy=N / 2):
    x, y = grid()
    return np.hypot(x - cx + 0.5, y - cy + 0.5), np.arctan2(y - cy + 0.5, x - cx + 0.5)


def canvas(col="#000000"):
    img = np.zeros((N, N, 3))
    img[:] = C(col)
    return img


def dither(t):
    """A gradient value t (array) → 0 / 1 per pixel with the 4×4 Bayer matrix."""
    x, y = grid()
    return (t > BAYER[y.astype(int) % 4, x.astype(int) % 4]).astype(float)


def bands(t, cols, dith=True):
    """t in [0, 1] → one of the colours, the steps dithered (a pixel-art gradient).
    dith True: ordered dithering everywhere; "edge": flat bands, a checker line between two."""
    k = len(cols) - 1
    v = np.clip(t, 0, 0.9999) * k
    i = v.astype(int)
    f = v - i
    if dith == "edge":
        x, y = grid()
        chk = ((x.astype(int) + y.astype(int)) % 2 == 0)
        i = i + ((f > 0.82) | ((f > 0.62) & chk))
    elif dith:
        i = i + dither(f).astype(int)
    else:
        i = i + (f > 0.5)
    i = np.clip(i, 0, k)
    pal = np.array([C(c) for c in cols])
    return pal[i]


def sky(top, bottom, horizon=1.0, cols=None):
    x, y = grid()
    t = np.clip(y / (N * horizon), 0, 1)
    if not cols:
        cols = [mix_hex(top, bottom, k / 6) for k in range(7)]
    return bands(t, cols, dith="edge")


def mix_hex(a, b, t):
    c = mix(C(a), C(b), t)
    return "#%02x%02x%02x" % tuple(int(round(v)) for v in c)


def shade(img, k):
    return np.clip(img * k, 0, 255)


# ── sprites ──────────────────────────────────────────────────────────────────
def stamp(img, rows, x, y, cmap, anchor="bottom", k=1):
    """Small pixel sprite (strings, one char per pixel, '.' = nothing), k × k pixels per
    char; (x, y) = its bottom centre (anchor bottom) or its top left (anchor topleft)."""
    h, w = len(rows) * k, max(len(r) for r in rows) * k
    if anchor == "bottom":
        x0, y0 = int(x - w // 2), int(y - h + 1)
    else:
        x0, y0 = int(x), int(y)
    for j, r in enumerate(rows):
        for i, ch in enumerate(r):
            if ch == "." or ch == " ":
                continue
            c = C(cmap[ch])
            for dy in range(k):
                for dx in range(k):
                    yy, xx = y0 + j * k + dy, x0 + i * k + dx
                    if 0 <= yy < N and 0 <= xx < N:
                        img[yy, xx] = c


def px(img, x, y, col):
    if 0 <= int(y) < N and 0 <= int(x) < N:
        img[int(y), int(x)] = C(col) if isinstance(col, str) else col


def rect(img, x0, y0, x1, y1, col):
    x0, x1 = max(0, int(x0)), min(N, int(x1))
    y0, y1 = max(0, int(y0)), min(N, int(y1))
    if x0 < x1 and y0 < y1:
        img[y0:y1, x0:x1] = C(col) if isinstance(col, str) else col


def scatter(n, seed, mask=None, mind=3.0, tries=4000):
    """Up to n points apart from each other (inside mask when given)."""
    r = np.random.default_rng(seed)
    pts = []
    for _ in range(tries):
        if len(pts) >= n:
            break
        x, y = r.integers(1, N - 1), r.integers(1, N - 1)
        if mask is not None and not mask[y, x]:
            continue
        if all((x - a) ** 2 + (y - b) ** 2 >= mind * mind for a, b in pts):
            pts.append((int(x), int(y)))
    return pts


# ── views from above (like the map) ──────────────────────────────────────────
WATER = ["#16246a", "#1f3a9a", "#2b55c7", "#3f76e4", "#5b95f0"]


def hillshade(h, k=7.0):
    nw = np.roll(np.roll(h, 1, 0), 1, 1)
    nw[0, :], nw[:, 0] = h[0, :], h[:, 0]
    return np.clip(1.0 + k * (h - nw), 0.62, 1.38)


def water(h, sea=0.0, deep=0.35, cols=WATER):
    d = np.clip((sea - h) / deep, 0, 1)
    return bands(1 - d, cols)


def land(h, img_land, sea=0.0, shade_k=7.0, cols=WATER, deep=0.35, beach=None, beach_w=0.03):
    img = np.where((h < sea)[..., None], water(h, sea, deep, cols), img_land)
    if beach is not None:
        b = (h >= sea) & (h < sea + beach_w)
        img[b] = C(beach)
    s = hillshade(np.maximum(h, sea), shade_k)
    lit = h >= sea
    img[lit] = img[lit] * s[lit][:, None]
    return np.clip(img, 0, 255)


def trees(img, pts, dark="#1f5a1f", light="#3f8f2f", trunk=None):
    for x, y in pts:
        px(img, x, y, dark)
        px(img, x + 1, y, dark)
        px(img, x, y + 1, dark)
        px(img, x + 1, y + 1, shade(C(dark), 0.75))
        px(img, x, y, light)


def outline_coast(img, h, sea=0.0, col="#e9dca2"):
    edge = (h >= sea) & ((np.roll(h, 1, 0) < sea) | (np.roll(h, -1, 0) < sea) |
                         (np.roll(h, 1, 1) < sea) | (np.roll(h, -1, 1) < sea))
    img[edge] = C(col)


def surface_biome(h, cols, lo=0.0, hi=1.0):
    t = np.clip((h - lo) / (hi - lo), 0, 1)
    return bands(t, cols)


def mushrooms_top(img, pts, big=False):
    for i, (x, y) in enumerate(pts):
        red = i % 3 != 2
        cap = "#c0392b" if red else "#9b6b43"
        hi = "#e74c3c" if red else "#b58457"
        if big:
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    if dx * dx + dy * dy <= 5:
                        px(img, x + dx, y + dy, cap)
            px(img, x - 1, y - 1, hi)
            if red:
                px(img, x + 1, y, "#f2e6d8")
                px(img, x - 1, y + 1, "#f2e6d8")
        else:
            for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
                px(img, x + dx, y + dy, cap)
            px(img, x, y, hi)


@scene("sinkhole", "Sinkhole", "Terrain", "hole pit crater cave deep")
def sc_sinkhole():
    r, a = radial(33, 31)
    base = fbm(N, 22, 1) * 0.5 + 0.35
    wob = 1 + 0.12 * (fbm(N, 10, 2) - 0.5)
    hole = r * wob < 15
    h = base.copy()
    h[hole] = 0.25 - (15 - r[hole] * wob[hole]) * 0.05
    g = surface_biome(base, ["#4f8f2f", "#5ea33a", "#6cb544"], 0.3, 0.9)
    img = land(h, g, sea=-10, shade_k=10)
    inner = hole
    depth = np.clip((15 - r * wob) / 15, 0, 1)
    wall = bands(depth, ["#7d7d7d", "#646464", "#4a4a4a", "#2c2c30", "#141418"])
    img[inner] = wall[inner]
    lit = inner & (a > 0.6) & (a < 2.6)                        # the far wall gets the light
    img[lit] = np.clip(img[lit] * 1.3, 0, 255)
    rim = (r * wob >= 15) & (r * wob < 16.2)
    img[rim] = C("#8a6a44")
    img[(r * wob < 3.2)] = C("#1b3d7a")                         # water at the bottom
    trees(img, scatter(26, 3, ~(r * wob < 18), 5))
    return img


@scene("giant_sinkhole", "Giant sinkhole", "Terrain", "hole pit crater huge deep void")
def sc_giant_sinkhole():
    r, a = radial(32, 32)
    wob = 1 + 0.10 * (fbm(N, 12, 5) - 0.5)
    rr = r * wob
    base = fbm(N, 20, 6) * 0.4 + 0.4
    g = surface_biome(base, ["#5a8d3a", "#6aa144", "#80b553"], 0.3, 0.9)
    img = land(base, g, sea=-10, shade_k=10)
    hole = rr < 24
    depth = np.clip((24 - rr) / 24, 0, 1)
    terr = (np.floor(depth * 7) / 7)                          # terraced walls
    wall = bands(terr, ["#8b8b8b", "#6f6f6f", "#555559", "#3a3a40", "#222228", "#101014", "#050508"], dith=False)
    img[hole] = wall[hole]
    lit = hole & (np.cos(a + 2.3) > 0.3)
    img[lit] = np.clip(img[lit] * 1.25, 0, 255)
    img[(rr >= 24) & (rr < 25.3)] = C("#9a7a4f")
    trees(img, scatter(14, 7, rr > 27, 5))
    return img


def ring_terrain(seed, R=17, w=7, valley_lake=False):
    r, a = radial()
    wob = 1 + 0.15 * (fbm(N, 9, seed) - 0.5)
    ring = np.exp(-((r * wob - R) / w) ** 2)
    h = 0.15 + 0.85 * ring * (0.75 + 0.5 * fbm(N, 6, seed + 1)) + 0.1 * fbm(N, 16, seed + 2)
    if valley_lake:
        h[r < R - w * 0.9] -= 0.25
    return h, r


@scene("mountain_ring", "Mountain ring", "Terrain", "ring circle crown peaks valley mountains")
def sc_mountain_ring():
    h, r = ring_terrain(11)
    g = surface_biome(h, ["#4c8c2c", "#5e9e3a", "#5f7a45", "#6a6a5e", "#77776b", "#838378", "#e8e8e8"], 0.15, 1.15)
    img = land(h, g, sea=-1, shade_k=8)
    trees(img, scatter(22, 12, (h < 0.3) & (r < 10), 4))
    return img


@scene("mountain_ring_lake", "Mountain ring with a lake", "Terrain", "ring circle crown peaks lake valley")
def sc_mountain_ring_lake():
    h, r = ring_terrain(21, valley_lake=True)
    h = h - 0.05
    g = surface_biome(h, ["#3f7f2c", "#5e9e3a", "#5f7a45", "#6a6a5e", "#77776b", "#838378", "#f0f0f0"], 0.0, 1.1)
    img = land(h, g, sea=0.0, shade_k=8, beach="#d8cf96", deep=0.25)
    trees(img, scatter(16, 22, (h > 0.03) & (h < 0.3), 4), "#24521f", "#3d7a2a")
    return img


@scene("crater_lake", "Crater lake", "Terrain", "volcano crater lake caldera ring")
def sc_crater():
    h, r = ring_terrain(31, R=13, w=6, valley_lake=True)
    h = h - 0.08 - 0.25 * np.clip((r - 22) / 10, 0, 1)
    g = surface_biome(h, ["#6b6b5e", "#7f7f72", "#959589", "#b0b0a5"], 0.0, 0.9)
    img = land(h, g, sea=0.0, shade_k=14, beach="#8f8778", deep=0.3,
               cols=["#0e3a4f", "#145c72", "#1e7f95", "#3aa7b5", "#6fd0d0"])
    return img


@scene("volcano", "Volcano", "Terrain", "lava magma volcano mountain fire")
def sc_volcano():
    r, a = radial()
    h = np.exp(-(r / 19) ** 2) * (0.9 + 0.2 * fbm(N, 8, 41)) - 0.15
    g = surface_biome(h, ["#4a4440", "#5a5450", "#6c6560", "#7d7570"], 0.0, 0.9)
    img = land(h, g, sea=0.0, shade_k=16, beach="#3a3634", deep=0.3)
    lava = r < 5.5 + 1.5 * (fbm(N, 4, 42) - 0.5)
    img[lava] = bands(fbm(N, 3, 43), ["#c0320b", "#ff6a00", "#ffb000", "#ffe066"])[lava]
    for k in range(5):                                          # lava running down
        ang = k * 1.3 + 0.4
        for t in range(6, 16):
            x = 32 + math.cos(ang + 0.05 * t) * t
            y = 32 + math.sin(ang + 0.05 * t) * t
            px(img, x, y, "#ff7a1a" if t < 12 else "#c0320b")
    return img


def island(seed, R=18, cx=32, cy=32, rough=0.25):
    r, a = radial(cx, cy)
    wob = 1 + rough * (fbm(N, 10, seed) - 0.5) * 2
    return 0.6 * (1 - r * wob / R) + 0.15 * fbm(N, 6, seed + 1) - 0.08


MYC = ["#6f5f73", "#7c6a80", "#8b7790", "#9a86a0"]


@scene("mushroom_island", "Mushroom island", "Terrain", "mushroom fields mycelium ocean island")
def sc_mushroom():
    h = island(51, R=17)
    g = surface_biome(h, MYC, 0.0, 0.6)
    img = land(h, g, beach="#8d7b92", deep=0.6)
    mushrooms_top(img, scatter(9, 52, h > 0.08, 6), big=True)
    return img


@scene("mushroom_island_huge", "Huge mushroom island", "Terrain", "mushroom fields mycelium ocean island huge large")
def sc_mushroom_huge():
    h = island(61, R=31, rough=0.3) + 0.05
    g = surface_biome(h, MYC, 0.0, 0.7)
    img = land(h, g, beach="#8d7b92", deep=0.5)
    mushrooms_top(img, scatter(22, 62, h > 0.06, 6), big=True)
    mushrooms_top(img, scatter(30, 63, h > 0.03, 3))
    return img


@scene("mushroom_island_tiny", "Tiny mushroom island", "Terrain", "mushroom fields mycelium small ocean")
def sc_mushroom_tiny():
    h = island(71, R=8, cx=30, cy=34)
    g = surface_biome(h, MYC, 0.0, 0.5)
    img = land(h, g, beach="#8d7b92", deep=1.0)
    mushrooms_top(img, [(29, 33)], big=True)
    mushrooms_top(img, [(33, 37)])
    return img


@scene("mushroom_archipelago", "Mushroom archipelago", "Terrain", "mushroom fields islands many ocean")
def sc_mushroom_arch():
    h = (fbm(N, 14, 81, 4) - 0.62) * 1.6
    g = surface_biome(h, MYC, 0.0, 0.4)
    img = land(h, g, beach="#8d7b92", deep=0.5)
    mushrooms_top(img, scatter(16, 82, h > 0.06, 5))
    return img


def green_island(seed, R, cx=32, cy=32):
    h = island(seed, R=R, cx=cx, cy=cy)
    g = surface_biome(h, ["#5e9e3a", "#4f8f2f", "#3f7f2c", "#7a7a6a", "#a0a0a0"], 0.0, 0.7)
    return h, g


@scene("lone_island", "Lone island", "Terrain", "island ocean survival small spawn")
def sc_lone_island():
    h, g = green_island(91, 11)
    img = land(h, g, beach="#e3d59a", deep=0.9)
    trees(img, scatter(6, 92, h > 0.08, 3))
    return img


@scene("archipelago", "Archipelago", "Terrain", "islands ocean many tropical")
def sc_archipelago():
    h = (fbm(N, 12, 101, 4) - 0.6) * 1.5
    g = surface_biome(h, ["#6cb544", "#5ea33a", "#4f8f2f"], 0.0, 0.3)
    img = land(h, g, beach="#e9dca2", deep=0.4, cols=["#0f4a8a", "#1667b0", "#1f8ad0", "#3ab0e0", "#6fd8ee"])
    trees(img, scatter(14, 102, h > 0.05, 4), "#2f7a2a", "#5cad3c")
    return img


@scene("atoll", "Atoll", "Terrain", "ring island lagoon coral tropical warm ocean")
def sc_atoll():
    r, a = radial()
    wob = 1 + 0.12 * (fbm(N, 7, 111) - 0.5)
    h = 0.12 - np.abs(r * wob - 17) * 0.05 + 0.03 * fbm(N, 4, 112)
    lagoon = r * wob < 15
    img = land(h, surface_biome(h, ["#d9cf8f", "#6cb544"], 0.0, 0.1), beach="#efe4b0", deep=0.25,
               cols=["#0b3d7a", "#1360a8", "#1f8ad0", "#46c0d8", "#8eeadf"])
    lg = lagoon & (h < 0)
    img[lg] = bands(1 - np.clip(-h / 0.6, 0, 1), ["#2bb5c8", "#5fd6d0", "#9ef0dc"])[lg]
    trees(img, scatter(10, 113, h > 0.07, 3), "#2f7a2a", "#5cad3c")
    return img


@scene("river_delta", "River delta", "Terrain", "river delta coast estuary water")
def sc_delta():
    x, y = grid()
    h = 0.35 - y / N * 0.55 + 0.12 * fbm(N, 10, 121)
    for k in range(4):                                         # branching channels
        cx = 32 + (k - 1.5) * 9 * np.clip(y / N * 1.6 - 0.2, 0, 1) + 4 * (noise1(N, 12, 122 + k)[y.astype(int)] - 0.5)
        h = np.where(np.abs(x - cx) < 1.4 + y / N * 1.5, np.minimum(h, -0.05), h)
    g = surface_biome(h, ["#6cb544", "#5ea33a", "#4f8f2f"], 0.0, 0.4)
    img = land(h, g, beach="#d8cf96", deep=0.4)
    trees(img, scatter(12, 123, h > 0.12, 4))
    return img


@scene("river_meander", "Meandering river", "Terrain", "river plains snake water")
def sc_meander():
    x, y = grid()
    cx = 32 + 14 * np.sin(y / 9.0) + 3 * (noise1(N, 10, 131)[y.astype(int)] - 0.5)
    h = 0.25 + 0.15 * fbm(N, 14, 132)
    h = np.where(np.abs(x - cx) < 2.2, -0.08, h)
    g = surface_biome(h, ["#7cbd4a", "#6cb544", "#5ea33a"], 0.1, 0.4)
    img = land(h, g, beach="#c8c08a", deep=0.3)
    trees(img, scatter(18, 133, np.abs(x - cx) > 5, 4))
    return img


@scene("canyon", "Canyon", "Terrain", "canyon ravine gorge badlands river cliff")
def sc_canyon():
    x, y = grid()
    cx = 32 + 10 * np.sin(y / 11.0)
    d = np.abs(x - cx)
    h = np.where(d < 7, 0.05 + d * 0.03, 0.6) + 0.08 * fbm(N, 8, 141)
    t = np.floor(h * 10) / 10
    g = bands(np.clip(t, 0, 1), ["#a0522d", "#c2703d", "#d9a066", "#b5651d", "#e0b07a", "#c97b48"], dith=False)
    img = land(h, g, sea=-1, shade_k=12)
    img[d < 1.6] = C("#2b55c7")
    return img


@scene("lake_forest", "Lake in a forest", "Terrain", "lake forest pond trees")
def sc_lake_forest():
    r, a = radial(30, 34)
    wob = 1 + 0.25 * (fbm(N, 8, 151) - 0.5)
    h = (r * wob - 12) * 0.02 + 0.05 * fbm(N, 10, 152)
    g = surface_biome(h, ["#3f7f2c", "#2f6f24"], 0.0, 0.4)
    img = land(h, g, beach="#7a8a4a", deep=0.25)
    trees(img, scatter(70, 153, h > 0.04, 3), "#1f5a1f", "#3a7f2c")
    return img


@scene("peninsula", "Peninsula", "Terrain", "coast peninsula cape ocean land")
def sc_peninsula():
    x, y = grid()
    h = 0.5 - np.abs(x - 30 - 6 * np.sin(y / 10)) / 14 + 0.25 * fbm(N, 9, 161) - y / N * 0.3
    g = surface_biome(h, ["#6cb544", "#5ea33a", "#4f8f2f", "#808080"], 0.0, 0.7)
    img = land(h, g, beach="#e3d59a", deep=0.5)
    trees(img, scatter(15, 162, h > 0.15, 4))
    return img


@scene("coastline", "Far lands of the coast", "Terrain", "coast continent ocean shore far spawn")
def sc_coast():
    x, y = grid()
    h = (x / N - 0.45) * 1.2 + 0.25 * (fbm(N, 14, 171) - 0.5)
    g = surface_biome(h, ["#6cb544", "#5ea33a", "#4f8f2f", "#7a7a6a", "#bdbdbd"], 0.0, 0.7)
    img = land(h, g, beach="#e3d59a", deep=0.5)
    trees(img, scatter(18, 172, h > 0.1, 4))
    return img


@scene("ocean_trench", "Deep ocean", "Terrain", "ocean deep sea water monument trench")
def sc_deep_ocean():
    h = -0.1 - 0.5 * fbm(N, 18, 181)
    return water(h, deep=0.6, cols=["#0a1446", "#13246a", "#1b3590", "#2447b0", "#3460c8"])


@scene("frozen_ocean", "Frozen ocean", "Biomes", "ice frozen ocean iceberg snow cold")
def sc_frozen_ocean():
    h = -0.3 * fbm(N, 14, 191)
    img = water(h, deep=0.4, cols=["#1d3a7a", "#2a5aa0", "#3f78c0"])
    ice = fbm(N, 6, 192) > 0.55
    img[ice] = bands(fbm(N, 3, 193), ["#a8c8f0", "#cfe3fa"])[ice]
    berg = fbm(N, 9, 194) > 0.68
    img[berg] = C("#eaf4ff")
    edge = berg & ~np.roll(berg, 1, 0)
    img[edge] = C("#ffffff")
    return img


def topdown_biome(seed, cols, tree_cols=None, n_trees=0, lo=0.2, hi=0.8, k=8):
    h = 0.2 + 0.6 * fbm(N, 14, seed)
    g = surface_biome(h, cols, lo, hi)
    img = land(h, g, sea=-1, shade_k=k)
    if tree_cols:
        trees(img, scatter(n_trees, seed + 1, None, 3.2), *tree_cols)
    return img


@scene("dark_forest_top", "Dark forest canopy", "Biomes", "dark forest roofed canopy mansion trees")
def sc_dark_forest():
    img = topdown_biome(201, ["#1f4a1a", "#28591f", "#2f6624"], None)
    for (x, y) in scatter(40, 202, None, 4):
        for dy in range(-2, 2):
            for dx in range(-2, 2):
                px(img, x + dx, y + dy, "#1a3d16" if (dx + dy) % 3 else "#2c6a22")
    for (x, y) in scatter(5, 203, None, 10):                      # big mushrooms
        mushrooms_top(img, [(x, y)], big=True)
    return img


@scene("cherry_canopy", "Cherry grove from above", "Biomes", "cherry blossom pink grove trees")
def sc_cherry_top():
    img = topdown_biome(211, ["#6aa144", "#7ab44e", "#88c05a"], None)
    for (x, y) in scatter(26, 212, None, 6):
        for dy in range(-3, 3):
            for dx in range(-3, 3):
                if dx * dx + dy * dy < 8:
                    px(img, x + dx, y + dy, "#f4a7c8" if (dx * 3 + dy) % 4 else "#ffd1e3")
        px(img, x - 1, y - 1, "#ffe3ee")
    return img


@scene("jungle_top", "Jungle canopy", "Biomes", "jungle tropical trees bamboo temple")
def sc_jungle_top():
    img = topdown_biome(221, ["#2d8a1f", "#35a024", "#3fb52c"], None)
    for (x, y) in scatter(60, 222, None, 3):
        px(img, x, y, "#1e6a14"); px(img, x + 1, y, "#4fc93a"); px(img, x, y + 1, "#1e6a14")
    for (x, y) in scatter(3, 223, None, 16):
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                if dx * dx + dy * dy <= 10:
                    px(img, x + dx, y + dy, "#1c5f12" if (dx + dy) % 2 else "#2e8a1d")
    return img


@scene("desert_dunes", "Desert dunes", "Biomes", "desert sand dunes hot pyramid cactus")
def sc_desert():
    x, y = grid()
    h = 0.4 + 0.2 * np.sin((x + y * 0.6) / 3.2 + 4 * fbm(N, 12, 231)) + 0.1 * fbm(N, 6, 232)
    g = surface_biome(h, ["#d7c27a", "#e4d18f", "#efe0a6"], 0.2, 0.7)
    img = land(h, g, sea=-1, shade_k=10)
    for (cx, cy) in scatter(8, 233, None, 8):
        px(img, cx, cy, "#2f7a2a"); px(img, cx, cy + 1, "#2f7a2a")
    return img


@scene("badlands_top", "Badlands plateaus", "Biomes", "badlands mesa terracotta red plateau")
def sc_badlands_top():
    h = 0.2 + 0.8 * fbm(N, 16, 241)
    t = np.floor(h * 12) / 12
    g = bands(t, ["#c2703d", "#e0b07a", "#a0522d", "#d9a066", "#b5651d", "#f0d2b0", "#8b4a2b"], dith=False)
    return land(h, g, sea=-1, shade_k=10)


@scene("ice_spikes_top", "Ice spikes", "Biomes", "ice spikes snow frozen cold")
def sc_ice_spikes_top():
    img = topdown_biome(251, ["#e8f0f8", "#f4f8fc", "#ffffff"], None, k=4)
    for (x, y) in scatter(16, 252, None, 6):
        for d in range(4):
            px(img, x + d, y + d, "#7fb2e8" if d else "#d9ecff")
            px(img, x + d + 1, y + d, "#a9cff5")
        px(img, x, y, "#ffffff")
    return img


@scene("swamp_top", "Swamp", "Biomes", "swamp marsh witch hut lily water")
def sc_swamp():
    h = (fbm(N, 9, 261) - 0.48) * 0.8
    g = surface_biome(h, ["#4c6b2a", "#56762f", "#5f8034"], 0.0, 0.3)
    img = land(h, g, deep=0.2, cols=["#2e4a3a", "#365a44", "#3f6a4c", "#4a7a55"])
    for (x, y) in scatter(14, 262, h < 0, 3):
        px(img, x, y, "#2f8a2f")
    trees(img, scatter(16, 263, h > 0.02, 4), "#2c4a1c", "#4a6a2c")
    return img


@scene("mangrove_top", "Mangrove swamp", "Biomes", "mangrove swamp roots water")
def sc_mangrove():
    h = (fbm(N, 7, 271) - 0.5) * 0.6
    img = land(h, surface_biome(h, ["#5a5a32", "#6b6b3a"], 0, 0.2), deep=0.2,
               cols=["#3a4a2e", "#46593a", "#526a44"])
    for (x, y) in scatter(30, 272, None, 4):
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                px(img, x + dx, y + dy, "#3f6f1f" if (dx + dy) % 2 else "#558a2a")
    return img


@scene("flower_field", "Flower field", "Biomes", "flowers meadow plains sunflower colourful")
def sc_flowers():
    img = topdown_biome(281, ["#6cb544", "#7cc24f", "#88cc5a"], None, k=5)
    cols = ["#ffd23f", "#e64a3f", "#ffffff", "#c86ff0", "#ff8fc0", "#4fa3ff"]
    r = np.random.default_rng(282)
    for (x, y) in scatter(160, 283, None, 1.6, 9000):
        px(img, x, y, cols[r.integers(len(cols))])
    return img


@scene("savanna_top", "Savanna plateau", "Biomes", "savanna acacia plateau dry")
def sc_savanna():
    h = 0.2 + 0.8 * fbm(N, 18, 291)
    t = np.where(h > 0.55, 1.0, 0.0)
    h = 0.3 + t * 0.4 + 0.05 * fbm(N, 5, 292)
    g = surface_biome(h, ["#9aa04a", "#a8ad55", "#b6b860"], 0.3, 0.75)
    img = land(h, g, sea=-1, shade_k=14)
    for (x, y) in scatter(12, 293, None, 6):
        for dx in range(-2, 3):
            px(img, x + dx, y, "#5f7a24")
        px(img, x, y - 1, "#7a9a30")
    return img


@scene("taiga_top", "Snowy taiga", "Biomes", "taiga spruce snow cold forest")
def sc_taiga_top():
    img = topdown_biome(301, ["#e6eef2", "#f2f6f8", "#ffffff"], None, k=5)
    for (x, y) in scatter(55, 302, None, 3.2):
        px(img, x, y, "#2c4f3a"); px(img, x + 1, y, "#24402f"); px(img, x, y + 1, "#24402f")
        px(img, x, y - 1, "#ffffff")
    return img


@scene("spawn_plains", "Plains spawn", "Terrain", "spawn plains village start grass")
def sc_spawn_plains():
    img = topdown_biome(311, ["#7cbd4a", "#6cb544", "#5ea33a"], ("#2f6f24", "#4f9f34"), 18, k=6)
    for d in range(4):                                          # the spawn marker
        px(img, 32 + d, 32, "#ffffff"); px(img, 32 - d, 32, "#ffffff")
        px(img, 32, 32 + d, "#ffffff"); px(img, 32, 32 - d, "#ffffff")
    px(img, 32, 32, "#e64a3f")
    return img


# ── side views (landscapes) ──────────────────────────────────────────────────
def ridge(img, base, amp, scale, seed, col, oct=3, jag=False, top=None, topw=1, snow=None):
    """Fills below a 1-D profile: y = base - amp · noise. top: colour of its first pixels;
    snow: (y limit, colour) — the pixels above the limit get it."""
    n = noise1(N, scale, seed, oct)
    if jag:
        n = 1 - np.abs(2 * n - 1)
        n = n ** 1.4
    prof = (base - amp * n).astype(int)
    c = C(col)
    for x in range(N):
        y0 = max(0, prof[x])
        img[y0:, x] = c
        if top is not None:
            img[y0:y0 + topw, x] = C(top)
        if snow is not None:
            ys, sc = snow
            if y0 < ys:
                img[y0:min(ys, y0 + 4 + (x * 7) % 3), x] = C(sc)
    return prof


def ground(img, base, scale, seed, amp=3, grass="#5ea33a", dirt="#7a5534", stone=None):
    prof = ridge(img, base, amp, scale, seed, dirt, oct=2, top=grass, topw=2)
    if stone:
        for x in range(N):
            img[min(N - 1, prof[x] + 7):, x] = C(stone)
    return prof


def sun(img, x, y, r, col, glow=None):
    yy, xx = np.mgrid[0:N, 0:N]
    d = np.hypot(xx - x, yy - y)
    if glow:
        g = np.clip(1 - (d - r) / (r * 3), 0, 1) * 0.35
        img[:] = img * (1 - g[..., None]) + C(glow) * g[..., None]
    img[d <= r] = C(col)


def stars(img, n, seed, ymax=40, col="#ffffff"):
    r = np.random.default_rng(seed)
    for _ in range(n):
        x, y = r.integers(0, N), r.integers(0, ymax)
        px(img, x, y, col if r.random() < 0.7 else "#a9b8ff")


def clouds(img, y, seed, col="#ffffff", n=3):
    r = np.random.default_rng(seed)
    for _ in range(n):
        x = r.integers(-6, N)
        yy = y + r.integers(-4, 5)
        w = r.integers(8, 16)
        rect(img, x, yy, x + w, yy + 2, col)
        rect(img, x + 2, yy - 1, x + w - 3, yy, col)


SPRITES = {
    "oak": (["..ggg..", ".gGggg.", "gGgggdg", "ggggddg", ".gddd..", "...t...", "...t..."],
            dict(g="#3f8f2f", G="#5cb040", d="#2a6a22", t="#6b4a2b")),
    "spruce": (["...g...", "..ggg..", "...g...", "..ggg..", ".ggggg.", "..ggg..", ".ggggg.", "ggggggg", "...t..."],
               dict(g="#2c5a3a", t="#5a3a20")),
    "spruce_snow": (["...w...", "..wgw..", "...g...", "..wgw..", ".ggggg.", "..wgw..", ".ggggg.", "ggggggg", "...t..."],
                    dict(g="#2c5a3a", w="#ffffff", t="#5a3a20")),
    "cherry": (["..ppp...", ".pPpppp.", "pPpppPpp", "ppppppp.", ".p.tt.p.", "....t...", "....t...", "....t..."],
               dict(p="#f4a7c8", P="#ffd1e3", t="#4a2a2a")),
    "palm": (["gg...gg", "..ggg..", ".g.t.g.", "...t...", "...t...", "....t..", "....t.."],
             dict(g="#3a9a2a", t="#8a6a3a")),
    "acacia": (["ggggggg.", ".gggggg.", "...t....", "....t...", "....t...", "...t...."],
               dict(g="#6a8a2a", t="#8a5a3a")),
    "cactus": ([".g.", "gg.", ".gg", ".g.", ".g.", ".g."], dict(g="#2f8a2a")),
    "mush_red": ([".rrrrr.", "rrwrrwr", "rrrrrrr", "...s...", "...s...", "...s..."],
                 dict(r="#c0392b", w="#f2e6d8", s="#e8dccb")),
    "mush_brown": (["bbbbbbbbb", ".bbbbbbb.", "....s....", "....s....", "....s....", "....s...."],
                   dict(b="#9b6b43", s="#e8dccb")),
    "house": (["..rrrr..", ".rrrrrr.", "rrrrrrrr", ".pwwwwp.", ".pwgwwp.", ".pwwdwp.", ".pwwdwp."],
              dict(r="#7a4a2a", p="#6b4a2b", w="#c8a46a", g="#9fd0ff", d="#4a3020")),
    "pyramid": (["......o......", ".....ooo.....", "....ooooo....", "...ooOoOoo...", "..ooooooooo..",
                 ".ooooooooooo.", "ooooooooooooo"], dict(o="#d9c27a", O="#c46a2a")),
}


def put(img, name, x, y, k=1):
    rows, cmap = SPRITES[name]
    stamp(img, rows, x, y, cmap, k=k)


@scene("snowy_peaks", "Snowy peaks", "Landscapes", "mountains peaks snow jagged high cold")
def sc_snowy_peaks():
    img = sky("#6aa0e8", "#cfe4ff")
    ridge(img, 40, 30, 12, 401, "#8a98b0", jag=True, snow=(30, "#ffffff"))
    ridge(img, 50, 24, 9, 402, "#6a7488", jag=True, snow=(36, "#f0f6ff"))
    p = ground(img, 60, 10, 403, grass="#e8f0f8", dirt="#9aa0a8")
    for x in (6, 15, 47, 58):
        put(img, "spruce_snow", x, p[x])
    return img


@scene("jagged_sunrise", "Peaks at sunrise", "Landscapes", "mountains jagged peaks sunrise dawn orange")
def sc_peaks_sunrise():
    img = sky("#2b2d6e", "#ff9e5e", cols=["#2b2d6e", "#6b3f8a", "#c2577a", "#ff9e5e", "#ffd08a"])
    sun(img, 44, 40, 5, "#fff2c0", glow="#ffcf80")
    ridge(img, 46, 32, 10, 411, "#3a2f5a", jag=True, snow=(26, "#e8c8e0"))
    ridge(img, 56, 18, 8, 412, "#2a2240", jag=True)
    ridge(img, 64, 6, 6, 413, "#18142a")
    return img


@scene("meadow", "Meadow", "Landscapes", "meadow flowers hills plains grass spring")
def sc_meadow():
    img = sky("#5fa8ff", "#d6ecff")
    clouds(img, 10, 421)
    ridge(img, 32, 10, 20, 422, "#a8c8e8")
    ridge(img, 42, 8, 16, 423, "#8cc858")
    p = ground(img, 52, 14, 424, grass="#6cb544")
    r = np.random.default_rng(425)
    for _ in range(70):
        x = r.integers(0, N)
        y = p[x] - 1 + r.integers(0, 8)
        px(img, x, y, ["#ffd23f", "#ffffff", "#e64a3f", "#c86ff0", "#ff8fc0"][r.integers(5)])
    put(img, "oak", 46, p[46] - 1, k=2)
    return img


@scene("cherry_grove", "Cherry grove", "Landscapes", "cherry blossom pink trees grove hills")
def sc_cherry():
    img = sky("#8ec5ff", "#ffe0ee")
    ridge(img, 34, 12, 18, 431, "#e2c4e0")
    ridge(img, 44, 8, 14, 434, "#c8a8c8")
    p = ground(img, 54, 12, 432, grass="#7ab44e")
    put(img, "cherry", 18, p[18] - 1, k=2)
    put(img, "cherry", 48, p[48] - 1, k=2)
    r = np.random.default_rng(433)
    for _ in range(40):
        px(img, r.integers(0, N), r.integers(14, N), "#ffd1e3")
    return img


@scene("taiga_forest", "Taiga forest", "Landscapes", "taiga spruce forest pine cold")
def sc_taiga():
    img = sky("#7aa6d6", "#d8e8f0")
    ridge(img, 34, 12, 14, 441, "#7a94a8")
    for x in range(-2, N, 6):
        put(img, "spruce", x + 3, 44 + (x * 5) % 4)
    p = ground(img, 58, 10, 442, grass="#4f7a3a", dirt="#5a4030")
    for x in (8, 30, 52):
        put(img, "spruce", x, p[x] - 1, k=2)
    return img


@scene("desert_pyramid", "Desert pyramid", "Structures", "desert pyramid temple sand loot treasure")
def sc_pyramid():
    img = sky("#5fb0ff", "#fff0c8")
    sun(img, 52, 11, 4, "#fff8d0", glow="#fff0a0")
    ridge(img, 46, 6, 16, 451, "#e9d39a")
    p = ground(img, 58, 18, 452, grass="#efe0a6", dirt="#d7c27a")
    put(img, "pyramid", 30, p[30] - 1, k=3)
    put(img, "cactus", 56, p[56] - 1, k=2)
    put(img, "cactus", 6, p[6] - 1)
    return img


@scene("jungle_temple", "Jungle temple", "Structures", "jungle temple ruins vines bamboo")
def sc_jungle_temple():
    img = sky("#6ac0ff", "#d8f8ff")
    ridge(img, 30, 10, 10, 461, "#2d7a1f")
    p = ground(img, 58, 12, 462, grass="#3fb52c", dirt="#5a4a2a")
    t = [".....mmm.....", "...mmMmmmm...", "..mmm...mmm..", ".mmmmmmmmmmm.", "mmMmm...mmMmm", "mmmmm...mmmmm",
         "mmmmmmmmmmmmm"]
    stamp(img, t, 32, p[32] - 1, dict(m="#7a8a6a", M="#4f8f3f"), k=3)
    for x in range(14, 52, 4):                                      # vines
        for k in range(5):
            px(img, x, p[32] - 18 + k, "#3a8a2a")
    put(img, "oak", 4, p[4] - 1)
    put(img, "oak", 60, p[60] - 1)
    return img


@scene("village", "Village", "Structures", "village houses plains villagers town")
def sc_village():
    img = sky("#5fa8ff", "#d6ecff")
    clouds(img, 8, 471)
    ridge(img, 36, 6, 18, 472, "#a8cce8")
    p = ground(img, 58, 20, 473, grass="#6cb544")
    put(img, "house", 16, p[16] - 1, k=2)
    put(img, "house", 44, p[44] - 1, k=2)
    put(img, "oak", 60, p[60] - 1)
    put(img, "oak", 30, p[30] - 1)
    rect(img, 0, p[30] + 2, N, p[30] + 3, "#b08a5a")               # the path
    return img


@scene("snowy_village", "Snowy village", "Structures", "village snow igloo cold houses")
def sc_snowy_village():
    img = sky("#9cc0e8", "#eef6ff")
    p = ground(img, 58, 20, 481, grass="#ffffff", dirt="#b0b8c0")
    for x in (18, 46):
        stamp(img, ["..wwww..", ".wwwwww.", "wwwwwwww", ".pssssp.", ".psgssp.", ".pssdsp."], x, p[x] - 1,
              dict(w="#ffffff", p="#5a3a20", s="#d8c8a8", g="#9fd0ff", d="#4a3020"), k=2)
    put(img, "spruce_snow", 3, p[3] - 1)
    put(img, "spruce_snow", 61, p[61] - 1)
    r = np.random.default_rng(482)
    for _ in range(30):
        px(img, r.integers(0, N), r.integers(0, 50), "#ffffff")
    return img


@scene("pillager_outpost", "Pillager outpost", "Structures", "outpost pillager tower crossbow")
def sc_outpost():
    img = sky("#7a8aa0", "#d0d8e0")
    p = ground(img, 60, 16, 491, grass="#6a9a3a")
    tower = ["wwwwwwwww", "w.w.w.w.w", "ddddddddd", ".dbbbbbd.", ".dbgbgbd.", ".dbbbbbd.", ".ddddddd.",
             ".dbbbbbd.", ".dbbgbbd.", ".dbbbbbd.", ".ddddddd.", ".dbbbbbd.", ".dbbbbbd.", ".dbbdbbd."]
    stamp(img, tower, 28, p[28] - 1, dict(w="#4a3a2a", d="#3a2a1a", b="#8a8a8a", g="#202020"), k=2)
    rect(img, 46, 6, 47, 22, "#5a4030")
    stamp(img, ["wwwwww", "wbbbbw", "wbwwbw", "wwwwww"], 47, 6, dict(w="#f0f0f0", b="#3a3a3a"), "topleft", k=2)
    return img


@scene("witch_hut", "Witch hut", "Structures", "witch hut swamp night")
def sc_witch():
    img = sky("#1a2238", "#4a5a5a")
    sun(img, 10, 9, 3, "#f0f0d0")
    stars(img, 20, 501, 30)
    rect(img, 0, 54, N, N, "#3a5a40")
    for x in range(0, N, 3):
        px(img, x, 54, "#5a8a50")
    hut = ["..rrrrrrrr..", ".rrrrrrrrrr.", "rrrrrrrrrrrr", ".ppwwwwwwpp.", ".pwwwgwwwwp.", ".pwwwwwwwwp.",
           ".pppppppppp.", ".l........l.", ".l........l.", ".l........l."]
    stamp(img, hut, 32, 57, dict(r="#3a2a2a", p="#4a3a2a", w="#7a6a4a", g="#ffd060", l="#3a2a1a"), k=2)
    for (x, y) in ((6, 55), (54, 56), (60, 55)):
        px(img, x, y, "#2f8a2f"); px(img, x + 1, y, "#2f8a2f")
    return img


@scene("igloo", "Igloo", "Structures", "igloo snow cold basement")
def sc_igloo():
    img = sky("#3f6fb0", "#a8c8ec")
    p = ground(img, 46, 18, 511, grass="#ffffff", dirt="#c8d4e0")
    ig = ["....wwwww....", "..wwwwwwwww..", ".wwwwwwwwwww.", "wwwwwwwwwwwww", "wwwwwlllwwwww", "wwwwwlddwwwww"]
    stamp(img, ig, 26, p[26] - 1, dict(w="#f4f8ff", l="#c0d8f0", d="#5a7aa0"), k=2)
    for x in range(14, 39):                                         # its shadow line
        px(img, x, p[26], "#b8c8dc")
    put(img, "spruce_snow", 56, p[56] - 1, k=2)
    for y in range(p[26] + 2, N - 8, 2):                            # the basement ladder
        px(img, 26, y, "#7a5a3a"); px(img, 27, y, "#7a5a3a")
    rect(img, 14, N - 9, 40, N, "#6a6a70")
    rect(img, 16, N - 7, 38, N - 1, "#a03030")
    rect(img, 18, N - 6, 22, N - 3, "#e0c040")
    return img


@scene("woodland_mansion", "Woodland mansion", "Structures", "mansion dark forest big building")
def sc_mansion():
    img = sky("#3a4a6a", "#8a9ab0")
    ridge(img, 40, 8, 10, 521, "#1a3a1a")
    rect(img, 0, 58, N, N, "#2a4a1a")
    m = ["....rrrrrrrrrrrrrrrrrrrrr....", "...rrrrrrrrrrrrrrrrrrrrrrr...", "..wwwwwwwwwwwwwwwwwwwwwwwww..",
         "..wgwwgwwgwwgwwgwwgwwgwwgww..", "..wwwwwwwwwwwwwwwwwwwwwwwww..", "rrrrrrrrrrrrrrrrrrrrrrrrrrrrr",
         "wwwwwwwwwwwwwwwwwwwwwwwwwwwww", "wgwwgwwgwwgwwwdwwwgwwgwwgwwgw", "wwwwwwwwwwwwwwdwwwwwwwwwwwwww"]
    stamp(img, m, 32, 58, dict(r="#3a2a20", w="#6a4a30", g="#ffd070", d="#2a1a10"), k=2)
    return img


@scene("ruined_portal", "Ruined portal", "Structures", "ruined portal obsidian crying nether gate")
def sc_ruined_portal():
    img = sky("#5a7ab0", "#c0d0e8")
    p = ground(img, 58, 14, 531, grass="#6a9a3a")
    port = [".oooo..", "o....o.", "o.....", "c....o.", "o....o.", "o....c.", "ooo.ooo"]
    stamp(img, port, 32, p[32] - 1, dict(o="#1a1028", c="#5a2a9a"), k=4)
    rect(img, 24, p[32] - 22, 40, p[32] - 4, "#7a3ac0")
    rect(img, 26, p[32] - 20, 30, p[32] - 16, "#a060e0")
    for (x, y) in ((12, p[12] - 1), (50, p[50] - 1), (54, p[54] - 2)):
        rect(img, x, y - 2, x + 3, y + 1, "#1a1028")
    for x in (16, 46, 20):
        px(img, x, p[x], "#ff7a1a"); px(img, x + 1, p[x], "#ffd060")
    return img


@scene("shipwreck", "Shipwreck", "Structures", "shipwreck ocean treasure map loot underwater")
def sc_shipwreck():
    img = sky("#1f6ab0", "#0a2a5a", cols=["#2a8ad0", "#2478bf", "#1f6ab0", "#195a9a", "#164e8a", "#0e3466", "#0a2448"])
    rect(img, 0, 54, N, N, "#c8b878")
    ship = ["...........m.....", "...........m.....", "..........sss....", "...........m.....",
            "hhhhhhhhhhhhhhhh.", ".hhwhhhhhhhwhhh..", "..hhhhhhhhhhhh...", "...hhhhhhhhhh...."]
    stamp(img, ship, 30, 57, dict(m="#5a4030", s="#c8c0a8", h="#6a4a30", w="#2a1a10"), k=3)
    r = np.random.default_rng(541)
    for _ in range(14):
        px(img, r.integers(0, N), r.integers(0, 30), "#7ab8f0")
    for x in (4, 58):
        for y in range(44, 56):
            px(img, x + (y % 2), y, "#2a8a4a")
    return img


@scene("buried_treasure", "Buried treasure", "Structures", "treasure chest beach map x loot")
def sc_treasure():
    img = sky("#4fb0ff", "#d0f0ff")
    rect(img, 0, 30, N, 38, "#2a8ad0")
    rect(img, 0, 38, N, N, "#e8d8a0")
    for x in range(0, N, 4):
        px(img, x, 30, "#7ac8f0"); px(img, x + 2, 34, "#5ab0e8")
    put(img, "palm", 54, 40, k=2)
    stamp(img, ["xx...xx", ".xx.xx.", "..xxx..", ".xx.xx.", "xx...xx"], 6, 42, dict(x="#c0392b"), "topleft", k=2)
    stamp(img, [".bbbbbbbb.", "bbbbbbbbbb", "bbbbggbbbb", "bbbbbbbbbb", "bbbbbbbbbb"], 30, 61,
          dict(b="#8a5a2a", g="#f0c040"), k=2)
    rect(img, 20, 54, 40, 55, "#5a3a1a")
    return img


@scene("ocean_monument", "Ocean monument", "Structures", "monument ocean guardian prismarine underwater")
def sc_monument():
    img = sky("#1b5a9a", "#071a3a", cols=["#2470b0", "#1f66a4", "#1b5a9a", "#134478", "#0c2f58", "#09243f", "#071a3a"])
    rect(img, 0, 58, N, N, "#2a3a4a")
    mon = ["......pPp......", ".....ppppp.....", "..p..ppppp..p..", ".ppp.ppLpp.ppp.", "ppppppppppppppp",
           "pPpppLpppLpppPp", "ppppppppppppppp", "ppppppppppppppp"]
    stamp(img, mon, 32, 58, dict(p="#3a8a7a", P="#6ac0a8", L="#c8f0e0"), k=4)
    r = np.random.default_rng(551)
    for _ in range(18):
        px(img, r.integers(0, N), r.integers(0, 24), "#4a90c8")
    return img


@scene("trial_chambers", "Trial chambers", "Structures", "trial chambers copper tuff vault spawner")
def sc_trial():
    img = canvas("#4a4a52")
    x, y = grid()
    tile = ((x.astype(int) // 8 + y.astype(int) // 8) % 2 == 0)
    img[tile] = C("#55555e")
    rect(img, 0, 0, N, 10, "#3a3a42")
    for cx in (12, 52):
        rect(img, cx - 3, 10, cx + 3, 54, "#b8693a")
        rect(img, cx - 2, 10, cx - 1, 54, "#d8894a")
    rect(img, 22, 30, 42, 50, "#3a3a42")                           # the vault
    rect(img, 24, 32, 40, 48, "#b8693a")
    rect(img, 27, 35, 37, 45, "#202028")
    rect(img, 30, 38, 34, 42, "#ffd060")
    rect(img, 0, 54, N, N, "#6a6a72")
    return img


@scene("trail_ruins", "Trail ruins", "Structures", "trail ruins archaeology suspicious gravel pottery")
def sc_trail():
    img = sky("#7aa8c8", "#d8e4e8")
    p = ground(img, 40, 16, 561, grass="#4f7a3a", dirt="#8a7a6a", stone="#7a7068")
    for (x0, w, hh) in ((6, 8, 8), (24, 6, 14), (42, 10, 6)):
        rect(img, x0, p[x0] - hh, x0 + w, p[x0] + 10, "#a0522d")
        rect(img, x0 + 1, p[x0] - hh + 1, x0 + w - 1, p[x0] - hh + 2, "#c2703d")
    stamp(img, ["..ooo..", ".ooOoo.", "ooOOOoo", "ooooooo", ".ooooo."], 32, 62, dict(o="#c87a4a", O="#f0c080"), k=2)
    return img


@scene("stronghold_portal", "End portal (12 eyes)", "Structures", "stronghold portal end eyes 12 frame")
def sc_portal():
    img = canvas("#3a3a3a")
    x, y = grid()
    brick = ((y.astype(int) // 4) % 2 * 2 + x.astype(int)) // 4 % 2 == 0
    img[brick] = C("#444444")
    img[(y.astype(int) % 4) == 0] = C("#2a2a2a")
    rect(img, 12, 12, 52, 52, "#2a2e2a")
    rect(img, 16, 16, 48, 48, "#0a0a14")
    r = np.random.default_rng(571)
    for _ in range(40):
        px(img, r.integers(17, 48), r.integers(17, 48), ["#3a8a7a", "#ffffff", "#5ac0b0"][r.integers(3)])
    for i in range(3):                                              # 12 frames, each with an eye
        for (fx, fy) in ((20 + i * 10, 12), (20 + i * 10, 48), (12, 20 + i * 10), (48, 20 + i * 10)):
            rect(img, fx, fy, fx + 4, fy + 4, "#c8c89a")
            rect(img, fx + 1, fy + 1, fx + 3, fy + 3, "#2a8a5a")
            px(img, fx + 1, fy + 1, "#7af0b0")
    return img


@scene("eye_of_ender", "Eye in the sky", "Structures", "eye stronghold throw find ender pearl")
def sc_eye():
    img = sky("#2a3a6a", "#b0c8e8")
    stars(img, 10, 581, 20)
    ground(img, 58, 16, 582, grass="#5ea33a")
    yy, xx = np.mgrid[0:N, 0:N]
    d = np.hypot(xx - 36, yy - 22)
    img[d < 11] = C("#1f6a46")
    img[d < 9] = C("#2a8a5a")
    img[d < 7] = C("#3ab07a")
    img[d < 3.2] = C("#0a2a1a")
    rect(img, 32, 16, 35, 18, "#c8ffe0")
    for k in range(10):                                             # its trail
        rect(img, 24 - k * 2, 32 + k * 2, 26 - k * 2, 34 + k * 2, "#a070d0" if k % 2 else "#d0a0ff")
    return img


# ── underground ──────────────────────────────────────────────────────────────
def cave(seed, wall="#5a5a5a", dark="#1a1a1e", r0=0.5):
    n = fbm(N, 14, seed, 4)
    x, y = grid()
    t = n + 0.25 * (np.abs(y - 34) / 34)
    img = np.where((t > r0)[..., None], bands(np.clip((t - r0) * 4, 0, 1), [wall, mix_hex(wall, "#000000", 0.35)]),
                   C(dark))
    edge = (t > r0) & (np.roll(t, -1, 0) <= r0)
    img[edge] = shade(C(wall), 1.3)
    return img, t


@scene("cave", "Cave", "Underground", "cave underground stone ores mining")
def sc_cave():
    img, rock, top, bot = cave2(601, "#6a6a6a", "#121214")
    r = np.random.default_rng(602)
    for col, n in (("#5ad8e8", 3), ("#ffd060", 3), ("#e04040", 3), ("#3ad070", 2)):
        for _ in range(n * 3):
            x, y = r.integers(0, N - 2), r.integers(0, N - 2)
            if rock[y, x] and rock[y + 1, x + 1]:
                rect(img, x, y, x + 2, y + 2, col)
    stamp(img, ["y", "o", "w", "w"], 18, 44, dict(y="#ffe080", o="#ff9a30", w="#6a4a2a"), "topleft")
    return img


@scene("lush_cave", "Lush cave", "Underground", "lush cave azalea glow berries moss")
def sc_lush():
    img, rock, top, bot = cave2(611, "#5a6a4a", "#122018", light="#ffd080")
    x, y = grid()
    moss = rock & ~np.roll(rock, 1, 0) & (y > 30)
    img[moss] = C("#5aa03a")
    for xx in range(0, N, 3):
        yt = int(14 + 10 * noise1(N, 14, 611)[xx])
        L = 4 + (xx * 7) % 9
        for k in range(L):
            px(img, xx, yt + k, "#3a8a2a")
        if L > 7:
            px(img, xx, yt + L - 2, "#ffb040")
    return img


@scene("dripstone_cave", "Dripstone cave", "Underground", "dripstone stalactite cave pointed")
def sc_drip():
    img, rock, top, bot = cave2(621, "#8a6a54", "#1e1612")
    n1 = noise1(N, 14, 621)
    n2 = noise1(N, 12, 622)
    for xx in range(2, N, 5):
        yt = int(14 + 10 * n1[xx])
        L = 4 + (xx * 5) % 8
        for k in range(L):
            w = max(0, 1 - k // 4)
            rect(img, xx - w, yt + k, xx + w + 1, yt + k + 1, "#a07a5a" if k < L - 2 else "#c8a080")
    for xx in range(4, N, 7):
        yb = int(46 + 10 * n2[xx])
        for k in range(3 + xx % 5):
            px(img, xx, yb - k, "#a07a5a")
    return img


@scene("amethyst_geode", "Amethyst geode", "Underground", "geode amethyst crystal purple")
def sc_geode():
    img = canvas("#4a4a4e")
    r, a = radial()
    wob = 1 + 0.1 * (fbm(N, 6, 631) - 0.5)
    rr = r * wob
    img[rr < 26] = C("#3a3a3a")
    img[rr < 23] = C("#d8d8d0")
    img[rr < 21] = C("#7a4ab0")
    img[rr < 18] = C("#1a1028")
    for (x, y) in scatter(18, 632, (rr > 15) & (rr < 18.5), 3):
        stamp(img, [".p.", "pPp", "pPp"], x, y, dict(p="#a070e0", P="#e0c0ff"))
    return img


@scene("lava_lake_cave", "Lava lake", "Underground", "lava lake cave fire deep")
def sc_lava_cave():
    img, rock, top, bot = cave2(641, "#4a4444", "#140c0a", light="#ff7020")
    x, y = grid()
    lava = (y > 42) & ~rock
    img[lava] = bands(fbm(N, 4, 642), ["#c0320b", "#ff6a00", "#ff9a20", "#ffd040"])[lava]
    return img


@scene("mineshaft", "Mineshaft", "Underground", "mineshaft rails tunnel wood cobweb")
def sc_mineshaft():
    img = canvas("#0c0a08")
    for k, (w, col) in enumerate(((30, "#3a3028"), (22, "#2e2620"), (15, "#241e1a"), (9, "#1a1612"), (4, "#100e0c"))):
        x0, x1, y0, y1 = 32 - w, 32 + w, 30 - w * 0.9, 30 + w * 1.05
        rect(img, x0, y0, x1, y1, col)
        wood = mix(C("#9a7442"), C("#2a2016"), k / 4)
        t = max(1, 3 - k // 2)
        rect(img, x0, y0, x0 + t, y1, wood)                           # the frame of each step
        rect(img, x1 - t, y0, x1, y1, wood)
        rect(img, x0, y0, x1, y0 + t, wood)
    for y in range(36, N):                                            # the rails, going away
        w = (y - 34) * 0.55
        px(img, 32 - w, y, "#8a8a8a"); px(img, 32 + w, y, "#8a8a8a")
        if y % 3 == 0:
            rect(img, 32 - w, y, 32 + w + 1, y + 1, "#6a4a2a")
    for (cx, cy) in ((6, 4), (50, 6)):
        stamp(img, ["w...w", ".w.w.", "..w..", ".w.w.", "w...w"], cx, cy, dict(w="#d8d8d8"), "topleft", k=2)
    px(img, 12, 28, "#ffd060"); px(img, 12, 29, "#ff9a30"); px(img, 12, 30, "#6a4a2a")
    return img


def cave2(seed, wall, dark, light="#ffb060"):
    """A cave passage: rock above and below, an open middle, lit from a torch."""
    x, y = grid()
    top = 14 + 10 * noise1(N, 14, seed)[x.astype(int)]
    bot = 46 + 10 * noise1(N, 12, seed + 1)[x.astype(int)]
    rock = (y < top) | (y > bot)
    img = canvas(dark)
    tex = bands(fbm(N, 4, seed + 2), [mix_hex(wall, "#000000", 0.25), wall, mix_hex(wall, "#ffffff", 0.12)])
    img[rock] = tex[rock]
    edge = rock & ~np.roll(rock, 1, 0) | rock & ~np.roll(rock, -1, 0)
    img[edge] = shade(C(wall), 1.25)
    d = np.hypot(x - 18, y - 34)
    glow = np.clip(1 - d / 34, 0, 1) * 0.5
    img = img * (1 - glow[..., None]) + C(light) * glow[..., None] * 0.6 + img * glow[..., None] * 0.6
    return np.clip(img, 0, 255), rock, top, bot


@scene("dungeon", "Dungeon", "Underground", "dungeon spawner mossy cobblestone chest monster")
def sc_dungeon():
    img = canvas("#5a5a5a")
    x, y = grid()
    moss = fbm(N, 4, 651) > 0.55
    img[moss] = C("#4a7a3a")
    img[(x.astype(int) % 4 == 0) | (y.astype(int) % 4 == 0)] = C("#3a3a3a")
    rect(img, 22, 22, 42, 42, "#1a1a20")                            # the spawner cage
    for k in range(22, 42, 3):
        rect(img, k, 22, k + 1, 42, "#2a2a3a")
        rect(img, 22, k, 42, k + 1, "#2a2a3a")
    rect(img, 29, 29, 35, 35, "#ff6a30")
    rect(img, 31, 31, 33, 33, "#ffd060")
    stamp(img, ["bbbbbb", "bbggbb", "bbbbbb"], 6, 50, dict(b="#8a5a2a", g="#f0c040"), "topleft")
    return img


@scene("ancient_city", "Ancient city", "Underground", "ancient city deep dark sculk warden")
def sc_ancient():
    img = canvas("#060a10")
    x, y = grid()
    rect(img, 0, 48, N, N, "#1a2228")
    for cx in (10, 54):
        rect(img, cx - 3, 18, cx + 3, 48, "#2a3440")
    rect(img, 18, 14, 46, 20, "#2a3440")
    rect(img, 22, 20, 42, 48, "#0e1a26")
    rect(img, 24, 22, 40, 46, "#0a3a4a")
    sk = fbm(N, 5, 661) > 0.6
    img[sk & (y > 46)] = C("#0a5a6a")
    for (px_, py_) in scatter(14, 662, (y > 44), 4):
        px(img, px_, py_, "#3af0f0")
    for k in range(8):
        px(img, 32, 24 + k * 3, "#5af8f8")
    return img


# ── the Nether ───────────────────────────────────────────────────────────────
def nether_bg(seed, top, mid, fog):
    img = sky(top, fog, cols=[top, mid, fog])
    roof = noise1(N, 8, seed + 1)
    for xx in range(N):
        img[:int(4 + 8 * roof[xx]), xx] = C(mix_hex(top, "#000000", 0.4))
    return img


@scene("nether_wastes", "Nether wastes", "Nether", "nether wastes netherrack lava hell")
def sc_nether_wastes():
    img = nether_bg(701, "#3a0a0a", "#7a1a1a", "#c04a2a")
    ridge(img, 44, 10, 10, 702, "#6a1a1a")
    rect(img, 0, 52, N, N, "#ff6a00")
    img[52:54, :] = C("#ffb040")
    ridge(img, 58, 6, 8, 703, "#5a1414")
    for k in range(4):
        px(img, 20 + k * 9, 12 + k, "#ffd060")
    return img


@scene("crimson_forest", "Crimson forest", "Nether", "crimson forest nether fungus red")
def sc_crimson():
    img = nether_bg(711, "#2a0610", "#5a0e1e", "#a0283a")
    p = ground(img, 54, 12, 712, grass="#a01a2a", dirt="#4a0e14")
    for x in (10, 30, 50):
        stamp(img, [".rrrrrr.", "rrrrrrrr", "rryrrryr", "...ss...", "...ss...", "...ss...", "...ss..."],
              x, p[x] - 1, dict(r="#b01a2a", y="#ffb040", s="#7a1a2a"))
    return img


@scene("warped_forest", "Warped forest", "Nether", "warped forest nether fungus teal blue")
def sc_warped():
    img = nether_bg(721, "#0a1a2a", "#1a2a4a", "#2a4a6a")
    p = ground(img, 54, 12, 722, grass="#1a8a7a", dirt="#2a1a3a")
    for x in (12, 32, 52):
        stamp(img, [".tttttt.", "tttttttt", "ttottott", "...ss...", "...ss...", "...ss...", "...ss..."],
              x, p[x] - 1, dict(t="#1aa08a", o="#ff8a3a", s="#3a5a8a"))
    r = np.random.default_rng(723)
    for _ in range(18):
        px(img, r.integers(0, N), r.integers(10, 50), "#9af0e0")
    return img


@scene("soul_sand_valley", "Soul sand valley", "Nether", "soul sand valley fossils blue fire nether")
def sc_soul():
    img = nether_bg(731, "#0a1a1e", "#1a3a40", "#3a6a70")
    p = ground(img, 52, 14, 732, grass="#4a3a2a", dirt="#3a2a20")
    for x in (20, 44):
        for k in range(4):
            rect(img, x - 6 + k * 3, p[x] - 12 + abs(k - 1.5) * 3, x - 5 + k * 3, p[x], "#e8e0c8")
        rect(img, x - 6, p[x] - 12, x + 6, p[x] - 11, "#e8e0c8")
    for x in (8, 32, 56):
        px(img, x, p[x] - 1, "#5ad8f8"); px(img, x, p[x] - 2, "#9af0ff")
    return img


@scene("basalt_deltas", "Basalt deltas", "Nether", "basalt deltas pillars grey nether")
def sc_basalt():
    img = nether_bg(741, "#1a1a1e", "#3a3a40", "#6a6070")
    p = ground(img, 54, 8, 742, grass="#4a4a50", dirt="#2a2a30")
    r = np.random.default_rng(743)
    for x in range(2, N, 7):
        hgt = r.integers(10, 30)
        rect(img, x, p[x] - hgt, x + 3, p[x], "#4a4a52")
        rect(img, x, p[x] - hgt, x + 1, p[x], "#6a6a74")
    for _ in range(40):
        px(img, r.integers(0, N), r.integers(10, 50), "#d8d0c8")
    return img


@scene("nether_fortress", "Nether fortress", "Nether", "fortress nether bridge blaze wither")
def sc_fortress():
    img = nether_bg(751, "#2a0808", "#5a1414", "#a03020")
    rect(img, 0, 56, N, N, "#ff6a00")
    rect(img, 0, 30, N, 34, "#3a1418")
    for x in range(2, N, 12):
        rect(img, x, 34, x + 4, 56, "#3a1418")
        rect(img, x + 1, 34, x + 2, 56, "#4a1c20")
    for x in range(0, N, 3):
        px(img, x, 29, "#3a1418")
    stamp(img, ["..yyy..", ".yyyyy.", "yyoyoyy", ".yyyyy.", "..y.y.."], 44, 26, dict(y="#ffc030", o="#7a3a00"))
    return img


@scene("bastion", "Bastion remnant", "Nether", "bastion piglin gold blackstone treasure")
def sc_bastion():
    img = nether_bg(761, "#1a0a0a", "#3a1414", "#702a20")
    rect(img, 0, 58, N, N, "#ff6a00")
    b = ["bb.bb.bb.bb.bb", "bbbbbbbbbbbbbb", "bbgbbbbbbbbgbb", "bbbbbbbbbbbbbb", "bbbbbddddbbbbb",
         "bbbbbdggdbbbbb", "bbbbbddddbbbbb", "bbbbbbbbbbbbbb", "bbbbbbbbbbbbbb"]
    stamp(img, b, 32, 58, dict(b="#2a2228", g="#ffc030", d="#4a3a40"), k=4)
    return img


# ── the End ──────────────────────────────────────────────────────────────────
def end_sky(seed):
    img = sky("#0a0612", "#2a1a3a", cols=["#05030a", "#0e0818", "#1a1028", "#2a1a3a"])
    stars(img, 30, seed, N, "#d8c8ff")
    return img


@scene("end_island", "End island", "End", "end island void main pillars")
def sc_end_island():
    img = end_sky(801)
    isl = [".....eeeeeeeeeeeeeeeeeee.....", "..eeeeeeeeeeeeeeeeeeeeeeeeee..", "eeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
           ".EEEEEEEEEEEEEEEEEEEEEEEEEEEE.", "..EEEEEEEEEEEEEEEEEEEEEEEEEE..", "....EEEEEEEEEEEEEEEEEEEEEE....",
           "......EEEEEEEEEEEEEEEEEE......", "........EEEEEEEEEEEEEE........", "..........EEEEEEEEEE..........",
           "............EEEEEE............"]
    stamp(img, isl, 32, 56, dict(e="#e8e4b0", E="#b8b488"))
    for (x, hgt) in ((8, 20), (16, 26), (24, 16), (40, 22), (48, 28), (56, 18)):
        rect(img, x, 47 - hgt, x + 3, 47, "#1a1028")
        rect(img, x, 47 - hgt, x + 1, 47, "#2a1a40")
        px(img, x + 1, 46 - hgt, "#ff80ff")
    return img


@scene("end_city", "End city", "End", "end city ship elytra purpur towers")
def sc_end_city():
    img = end_sky(811)
    rect(img, 0, 58, N, N, "#d8d4a0")
    for (x0, w, top) in ((14, 10, 18), (30, 8, 8), (44, 10, 24)):
        rect(img, x0, top, x0 + w, 58, "#a070a8")
        rect(img, x0, top, x0 + 2, 58, "#c090c8")
        rect(img, x0 - 2, top, x0 + w + 2, top + 2, "#7a4a80")
        for yy in range(top + 5, 56, 6):
            rect(img, x0 + 3, yy, x0 + 5, yy + 2, "#2a1a3a")
    rect(img, 33, 2, 35, 8, "#7a4a80")
    return img


@scene("end_ship", "End ship (elytra)", "End", "end ship elytra flying wings")
def sc_end_ship():
    img = end_sky(821)
    ship = ["........m........", "........m........", "......sssss......", "........m........",
            "ppppppppppppppppp", ".ppPppppppppPppp.", "..ppppppppppppp..", "....ppppppppp...."]
    stamp(img, ship, 32, 58, dict(m="#7a4a80", s="#3a2a4a", p="#a070a8", P="#2a1a3a"), k=3)
    stamp(img, ["g.....g", "gg...gg", "ggg.ggg", ".ggggg.", "..g.g.."], 32, 22, dict(g="#a8b0d8"), k=2)
    return img


@scene("end_gateway", "End gateway", "End", "end gateway portal outer islands beam")
def sc_gateway():
    img = end_sky(831)
    rect(img, 22, 22, 42, 42, "#1a1028")
    rect(img, 26, 26, 38, 38, "#05030a")
    r = np.random.default_rng(832)
    for _ in range(16):
        px(img, r.integers(26, 38), r.integers(26, 38), "#5ac0b0")
    for y in range(0, 22):
        rect(img, 31, y, 33, y + 1, "#d8c8ff" if y % 3 else "#ffffff")
    rect(img, 12, 46, 52, 52, "#e8e4b0")
    rect(img, 18, 52, 46, 57, "#b8b488")
    rect(img, 26, 57, 38, 60, "#9a9670")
    return img


@scene("chorus_forest", "Chorus forest", "End", "chorus plants outer end islands purple")
def sc_chorus():
    img = end_sky(841)
    rect(img, 0, 56, N, N, "#e8e4b0")
    r = np.random.default_rng(842)
    for x0 in (8, 24, 42, 56):
        x, y = x0, 55
        for k in range(24):
            rect(img, x, y, x + 2, y + 1, "#7a4a8a")
            if r.random() < 0.3:
                x += 2 if r.random() < 0.5 else -2
            else:
                y -= 1
        rect(img, x - 1, y - 2, x + 3, y + 1, "#e0b0ff")
    return img


@scene("outer_islands", "Outer islands", "End", "end outer islands floating void")
def sc_outer():
    img = end_sky(851)
    for (cx, cy, w) in ((14, 20, 10), (40, 34, 18), (54, 14, 7), (20, 48, 12)):
        for k in range(4):
            rect(img, cx - w // 2 + k, cy + k, cx + w // 2 - k, cy + k + 1, "#e8e4b0" if k == 0 else "#b8b488")
    return img


# ── SSA itself: abstract pictures for searches ───────────────────────────────
@scene("noise_crunch", "Crunchiness", "Abstract", "crunchiness noise jagged ridges")
def sc_crunch():
    n = fbm(N, 6, 901, 5)
    n = 1 - np.abs(2 * n - 1)
    return bands(n ** 2, ["#0b1026", "#24306a", "#4a5ab0", "#8a7ae0", "#e0c0ff"])


@scene("climate_map", "Climate map", "Abstract", "climate temperature humidity continentalness noise")
def sc_climate():
    return bands(fbm(N, 16, 911, 4), ["#2c4bb8", "#3aa0d8", "#5ad0a0", "#e0e070", "#f0a040", "#d04030"])


@scene("biome_map", "Biome map", "Abstract", "biomes map colourful world overview")
def sc_biome_map():
    v = fbm(N, 12, 921)
    t = fbm(N, 20, 922)
    pal = np.array([C(c) for c in ("#2b55c7", "#e3d59a", "#6cb544", "#3f7f2c", "#d7c27a", "#9aa04a",
                                   "#ffffff", "#c2703d", "#2d8a1f", "#8b7790")])
    idx = np.clip((v * 6).astype(int) + (t > 0.55) * 3, 0, len(pal) - 1)
    img = pal[idx]
    return land(v, img, sea=0.35, shade_k=0)


@scene("spiral", "Spiral search", "Abstract", "spiral search scan seeds")
def sc_spiral():
    img = canvas("#0c1020")
    for k in range(900):
        t = k / 900 * 6 * math.pi
        rr = t * 1.55
        x = 32 + rr * math.cos(t)
        y = 32 + rr * math.sin(t)
        c = mix(C("#3ad0ff"), C("#ff6ad0"), k / 900)
        px(img, x, y, c)
    return img


@scene("seed_digits", "Seed numbers", "Abstract", "seed number digits binary random")
def sc_digits():
    img = canvas("#07140c")
    r = np.random.default_rng(941)
    for x in range(0, N, 4):
        L = r.integers(6, 30)
        y0 = r.integers(-10, N)
        for k in range(L):
            yy = y0 + k * 3
            c = mix(C("#0a3a1a"), C("#5aff8a"), k / L)
            px(img, x, yy, c); px(img, x + 1, yy, c * 0.7); px(img, x, yy + 1, c * 0.8)
    return img


@scene("night_sky", "Starry night", "Abstract", "night stars sky moon calm")
def sc_night():
    img = sky("#070a1a", "#1a2a5a")
    stars(img, 60, 951, N)
    sun(img, 46, 16, 5, "#e8e8d0")
    rect(img, 44, 14, 46, 16, "#c8c8b0")
    ridge(img, 60, 8, 14, 952, "#05070f")
    return img


@scene("compass", "Compass", "Abstract", "compass find locate direction north")
def sc_compass():
    img = canvas("#1a1e2a")
    r, a = radial()
    img[r < 27] = C("#6a4a2a")
    img[r < 25] = C("#9a7a4a")
    img[r < 22] = C("#efe8d8")
    for k in range(8):
        t = k / 8 * 2 * math.pi
        px(img, 32 + 19 * math.cos(t), 32 + 19 * math.sin(t), "#8a8070")
    for k in range(-15, 16):
        w = max(0, 3 - abs(k) // 5)
        c = "#d0402b" if k < 0 else "#4a4a5a"
        rect(img, 32 - w + k * 0.45, 32 + k, 33 + w + k * 0.45, 33 + k, c)
    rect(img, 31, 31, 34, 34, "#202020")
    return img


@scene("beacon", "Beacon beam", "Abstract", "beacon light beam spawn base")
def sc_beacon():
    img = sky("#0a1028", "#2a3a6a")
    stars(img, 25, 961, 40)
    for y in range(0, 50):
        img[y, 30:34] = mix(C("#9ad8ff"), C("#ffffff"), y / 50)
        img[y, 29] = img[y, 34] = C("#4a7ab0")
    rect(img, 26, 50, 38, 56, "#5ad8e8")
    rect(img, 22, 56, 42, N, "#d8d8d8")
    return img


@scene("ssa_seed", "Sigil pattern", "Abstract", "sigil pattern symmetric seed ssa")
def sc_sigil():
    img = canvas("#141a2e")
    r = np.random.default_rng(974)
    cell, n = 10, 5
    pad = (N - cell * n) // 2
    for y in range(n):
        for x in range(3):
            if r.random() < 0.5:
                c = mix(C("#5ad0ff"), C("#b07aff"), y / n)
                for xx in {x, n - 1 - x}:
                    rect(img, pad + xx * cell + 1, pad + y * cell + 1, pad + xx * cell + cell - 1,
                         pad + y * cell + cell - 1, c)
    return img


def sink(base_cols, hole_cols, seed, rim="#8a6a44", R=15, sea=-10):
    r, a = radial(32, 32)
    wob = 1 + 0.12 * (fbm(N, 10, seed) - 0.5)
    rr = r * wob
    base = fbm(N, 20, seed + 1) * 0.5 + 0.35
    img = land(base, surface_biome(base, base_cols, 0.3, 0.9), sea=sea, shade_k=10)
    hole = rr < R
    depth = np.clip((R - rr) / R, 0, 1)
    img[hole] = bands(depth, hole_cols)[hole]
    lit = hole & (a > 0.6) & (a < 2.6)
    img[lit] = np.clip(img[lit] * 1.3, 0, 255)
    img[(rr >= R) & (rr < R + 1.2)] = C(rim)
    return img, rr


@scene("diamonds", "Diamonds", "Underground", "diamond ore deep mining treasure")
def sc_diamonds():
    img = canvas("#4a4a52")
    x, y = grid()
    img[:] = bands(fbm(N, 5, 631 + 7), ["#3a3a42", "#45454e", "#52525c"])
    for (cx, cy) in ((14, 16), (40, 12), (28, 36), (48, 44), (10, 46)):
        rect(img, cx, cy, cx + 10, cy + 10, "#5a5a64")
        for (dx, dy) in ((2, 2), (6, 3), (3, 6), (7, 7)):
            rect(img, cx + dx, cy + dy, cx + dx + 2, cy + dy + 2, "#5af0f0")
            px(img, cx + dx, cy + dy, "#d8ffff")
    return img

@scene("ancient_debris", "Ancient debris", "Nether", "ancient debris netherite nether mining")
def sc_debris():
    img = bands(fbm(N, 5, 641), ["#5a1818", "#6a2020", "#7a2a2a"])
    for (cx, cy) in ((20, 20), (36, 34)):
        rect(img, cx, cy, cx + 14, cy + 14, "#4a3a34")
        for k in range(0, 14, 3):
            rect(img, cx + k, cy, cx + k + 1, cy + 14, "#6a564c")
        rect(img, cx, cy, cx + 14, cy + 2, "#5a4840")
    return img

@scene("sinkhole_desert", "Desert sinkhole", "Terrain", "sinkhole desert sand hole pit")
def sc_sink_desert():
    img, rr = sink(["#d7c27a", "#e4d18f", "#efe0a6"], ["#c8a868", "#a8885a", "#7a6448", "#3a3028", "#141010"], 1001,
                   rim="#b89a5a")
    for (x, y) in scatter(8, 1002, rr > 19, 7):
        px(img, x, y, "#2f7a2a"); px(img, x, y + 1, "#2f7a2a")
    return img

@scene("sinkhole_snow", "Snowy sinkhole", "Terrain", "sinkhole snow ice hole pit cold")
def sc_sink_snow():
    img, rr = sink(["#e8f0f8", "#f4f8fc", "#ffffff"], ["#b8c8d8", "#8a9aaa", "#5a6878", "#2a3240", "#0c1018"], 1011,
                   rim="#c8d8e8")
    for (x, y) in scatter(24, 1012, rr > 18, 4):
        px(img, x, y, "#2c4f3a"); px(img, x + 1, y, "#24402f"); px(img, x, y - 1, "#ffffff")
    img[rr < 3] = C("#a8d0f8")
    return img

@scene("sinkhole_double", "Twin sinkholes", "Terrain", "sinkhole two double holes pit")
def sc_sink_twin():
    base = fbm(N, 20, 1021) * 0.5 + 0.35
    img = land(base, surface_biome(base, ["#4f8f2f", "#5ea33a", "#6cb544"], 0.3, 0.9), sea=-10, shade_k=10)
    for (cx, cy, R) in ((20, 22, 11), (44, 42, 9)):
        r, a = radial(cx, cy)
        rr = r * (1 + 0.12 * (fbm(N, 8, 1022 + cx) - 0.5))
        hole = rr < R
        img[hole] = bands(np.clip((R - rr) / R, 0, 1), ["#7d7d7d", "#5a5a5a", "#34343a", "#141418"])[hole]
        img[(rr >= R) & (rr < R + 1.2)] = C("#8a6a44")
    trees(img, scatter(16, 1023, None, 6))
    return img

@scene("mountain_ring_snowy", "Snowy mountain crown", "Terrain", "ring crown peaks snow cold circle")
def sc_ring_snow():
    h, r = ring_terrain(1031, R=18, w=6)
    g = surface_biome(h, ["#dce6ee", "#c8d4de", "#a8b4c0", "#e8eef4", "#ffffff"], 0.15, 0.95)
    img = land(h, g, sea=-1, shade_k=16)
    for (x, y) in scatter(14, 1032, (h < 0.3) & (r < 11), 4):
        px(img, x, y, "#2c4f3a"); px(img, x, y - 1, "#ffffff")
    return img

@scene("badlands_crater", "Badlands crater", "Terrain", "badlands mesa crater ring terracotta red")
def sc_badlands_crater():
    h, r = ring_terrain(1041, R=16, w=7)
    t = np.floor(h * 10) / 10
    g = bands(np.clip(t, 0, 1), ["#c2703d", "#e0b07a", "#a0522d", "#d9a066", "#b5651d", "#f0d2b0"], dith=False)
    return land(h, g, sea=-1, shade_k=12)

@scene("cherry_island", "Cherry island", "Terrain", "cherry island pink ocean blossom")
def sc_cherry_island():
    h = island(1051, R=17)
    img = land(h, surface_biome(h, ["#6aa144", "#7ab44e", "#88c05a"], 0.0, 0.6), beach="#e3d59a", deep=0.6)
    for (x, y) in scatter(8, 1052, h > 0.12, 5):
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                if dx * dx + dy * dy <= 5:
                    px(img, x + dx, y + dy, "#f4a7c8" if (dx + dy) % 3 else "#ffd1e3")
    return img

@scene("jungle_island", "Jungle island", "Terrain", "jungle island tropical ocean")
def sc_jungle_island():
    h = island(1061, R=19)
    img = land(h, surface_biome(h, ["#2d8a1f", "#35a024", "#3fb52c"], 0.0, 0.6), beach="#efe4b0", deep=0.6,
               cols=["#0b3d7a", "#1360a8", "#1f8ad0", "#46c0d8", "#8eeadf"])
    for (x, y) in scatter(40, 1062, h > 0.06, 3):
        px(img, x, y, "#1e6a14"); px(img, x + 1, y, "#4fc93a"); px(img, x, y + 1, "#1e6a14")
    return img

@scene("desert_island", "Desert island", "Terrain", "desert island sand ocean cactus")
def sc_desert_island():
    h = island(1071, R=15)
    img = land(h, surface_biome(h, ["#d7c27a", "#e4d18f", "#efe0a6"], 0.0, 0.5), deep=0.7)
    for (x, y) in scatter(5, 1072, h > 0.08, 5):
        px(img, x, y, "#2f7a2a"); px(img, x, y + 1, "#2f7a2a")
    return img

@scene("twin_islands", "Twin islands", "Terrain", "two islands twin ocean")
def sc_twin_islands():
    h = np.maximum(island(1081, R=13, cx=20, cy=24), island(1082, R=11, cx=45, cy=42))
    g = surface_biome(h, ["#5e9e3a", "#4f8f2f", "#3f7f2c", "#7a7a6a"], 0.0, 0.6)
    img = land(h, g, beach="#e3d59a", deep=0.8)
    trees(img, scatter(14, 1083, h > 0.08, 3))
    return img

@scene("mushroom_ring_island", "Mushroom atoll", "Terrain", "mushroom fields ring island lagoon mycelium")
def sc_mush_atoll():
    r, a = radial()
    wob = 1 + 0.15 * (fbm(N, 8, 1091) - 0.5)
    h = 0.2 - np.abs(r * wob - 17) * 0.04 + 0.04 * fbm(N, 4, 1092)
    img = land(h, surface_biome(h, MYC, 0.0, 0.2), beach="#8d7b92", deep=0.5)
    mushrooms_top(img, scatter(10, 1093, h > 0.08, 5), big=True)
    return img

@scene("mushroom_coast", "Mushroom shore", "Terrain", "mushroom fields coast continent mycelium")
def sc_mush_coast():
    x, y = grid()
    h = (x / N - 0.5) * 1.0 + 0.25 * (fbm(N, 12, 1101) - 0.5)
    my = (x / N) < 0.75
    g = np.where(my[..., None], surface_biome(h, MYC, 0.0, 0.5),
                 surface_biome(h, ["#6cb544", "#5ea33a"], 0.0, 0.5))
    img = land(h, g, beach="#8d7b92", deep=0.5)
    mushrooms_top(img, scatter(10, 1102, (h > 0.05) & my, 6), big=True)
    trees(img, scatter(8, 1103, (h > 0.05) & ~my, 4))
    return img

@scene("oasis", "Desert oasis", "Terrain", "oasis desert lake palm water")
def sc_oasis():
    r, a = radial(30, 32)
    wob = 1 + 0.2 * (fbm(N, 7, 1111) - 0.5)
    h = (r * wob - 8) * 0.03 + 0.1 * fbm(N, 14, 1112)
    g = np.where((r < 13)[..., None], surface_biome(h, ["#5ea33a", "#6cb544"], 0, 0.3),
                 surface_biome(h, ["#d7c27a", "#e4d18f", "#efe0a6"], 0.0, 0.6))
    img = land(h, g, beach="#e9dca2", deep=0.2, cols=["#1f6ab0", "#2a8ad0", "#46b0e0", "#7ad0f0"])
    trees(img, scatter(7, 1113, (r > 9) & (r < 13), 4), "#2f7a2a", "#5cad3c")
    return img

@scene("mountain_pass", "River through mountains", "Terrain", "river valley mountains gorge")
def sc_mountain_pass():
    x, y = grid()
    cx = 32 + 8 * np.sin(y / 12.0)
    d = np.abs(x - cx)
    h = np.clip(d / 26, 0, 1) ** 0.7 * 0.9 + 0.2 * fbm(N, 7, 1121) - 0.05
    h = np.where(d < 2, -0.05, h)
    g = surface_biome(h, ["#5e9e3a", "#4f8f2f", "#7a7a6a", "#9a9a9a", "#d8d8d8", "#ffffff"], 0.0, 0.95)
    return land(h, g, beach="#8a9a6a", deep=0.2, shade_k=16)

@scene("lone_peak", "Lone peak", "Terrain", "mountain peak summit high single")
def sc_lone_peak():
    r, a = radial(32, 32)
    h = np.exp(-(r / 17) ** 2) * (0.85 + 0.3 * fbm(N, 6, 1131)) + 0.1 * fbm(N, 14, 1132)
    g = surface_biome(h, ["#5e9e3a", "#4f8f2f", "#7a7a6a", "#9a9a9a", "#e0e0e0", "#ffffff"], 0.1, 0.95)
    img = land(h, g, sea=-1, shade_k=18)
    trees(img, scatter(16, 1133, h < 0.3, 4))
    return img

@scene("mesa_plateau", "Mesa plateau", "Terrain", "plateau mesa flat top cliff badlands")
def sc_mesa():
    r, a = radial(32, 32)
    wob = 1 + 0.25 * (fbm(N, 9, 1141) - 0.5)
    top = r * wob < 17
    h = np.where(top, 0.8, 0.2) + 0.05 * fbm(N, 5, 1142)
    t = np.floor(h * 10) / 10
    g = bands(np.clip(t, 0, 1), ["#c2703d", "#e0b07a", "#a0522d", "#d9a066", "#b5651d", "#f0d2b0"], dith=False)
    g[top] = C("#9aa04a")
    return land(h, g, sea=-1, shade_k=12)

@scene("coral_reef", "Coral reef", "Terrain", "coral reef warm ocean tropical colourful")
def sc_coral():
    h = -0.06 - 0.12 * fbm(N, 10, 1151)
    img = water(h, deep=0.3, cols=["#1f8ad0", "#2aa8d8", "#46c0d8", "#7ad8e0"])
    cols = ["#ff5a8a", "#ffd23f", "#6a5aff", "#ff8a3a", "#3ad8a0", "#c85aff"]
    r = np.random.default_rng(1152)
    for (x, y) in scatter(60, 1153, None, 2.6):
        c = cols[r.integers(len(cols))]
        px(img, x, y, c); px(img, x + 1, y, c); px(img, x, y + 1, c)
    return img

@scene("big_lake", "Great lake", "Terrain", "lake big inland water huge")
def sc_big_lake():
    r, a = radial(32, 32)
    wob = 1 + 0.35 * (fbm(N, 12, 1161) - 0.5)
    h = (r * wob - 22) * 0.02 + 0.06 * fbm(N, 6, 1162)
    img = land(h, surface_biome(h, ["#6cb544", "#5ea33a", "#4f8f2f"], 0.0, 0.3), beach="#d8cf96", deep=0.3)
    trees(img, scatter(18, 1163, h > 0.04, 4))
    return img

@scene("spawn_island", "Spawn on an island", "Terrain", "spawn island ocean survival start")
def sc_spawn_island():
    h, g = green_island(1171, 14)
    img = land(h, g, beach="#e3d59a", deep=0.9)
    trees(img, scatter(7, 1172, h > 0.1, 4))
    for d in range(4):
        px(img, 32 + d, 32, "#ffffff"); px(img, 32 - d, 32, "#ffffff")
        px(img, 32, 32 + d, "#ffffff"); px(img, 32, 32 - d, "#ffffff")
    px(img, 32, 32, "#e64a3f")
    return img

@scene("far_spawn", "Far from spawn", "Terrain", "far spawn distance travel coordinates")
def sc_far_spawn():
    img = topdown_biome(1181, ["#7cbd4a", "#6cb544", "#5ea33a"], ("#2f6f24", "#4f9f34"), 14, k=6)
    for k in range(0, 46, 3):                                       # a dotted route to a far point
        px(img, 8 + k, 56 - k, "#ffffff")
    rect(img, 6, 54, 10, 58, "#e64a3f")
    rect(img, 52, 8, 58, 14, "#ffd23f")
    rect(img, 54, 10, 56, 12, "#ffffff")
    return img

@scene("world_border", "World border", "Terrain", "world border edge limit far lands")
def sc_border():
    img = topdown_biome(1191, ["#7cbd4a", "#6cb544", "#5ea33a"], ("#2f6f24", "#4f9f34"), 12, k=6)
    x, y = grid()
    out = x > 44
    img[out] = np.clip(img[out] * 0.45 + C("#3a5aff") * 0.25, 0, 255)
    stripe = out & (((x + y).astype(int) // 3) % 2 == 0)
    img[stripe] = np.clip(img[stripe] + C("#202850"), 0, 255)
    rect(img, 44, 0, 45, N, "#8aa0ff")
    return img

@scene("slime_chunks", "Slime chunks", "Terrain", "slime chunks grid farm green")
def sc_slime():
    img = topdown_biome(1201, ["#7cbd4a", "#6cb544", "#5ea33a"], None, k=5)
    r = np.random.default_rng(1202)
    for cy in range(0, N, 8):
        for cx in range(0, N, 8):
            if r.random() < 0.18:
                img[cy:cy + 8, cx:cx + 8] = np.clip(img[cy:cy + 8, cx:cx + 8] * 0.5 + C("#5aff5a") * 0.5, 0, 255)
    x, y = grid()
    img[(x.astype(int) % 8 == 0) | (y.astype(int) % 8 == 0)] *= 0.8
    return img

@scene("village_top", "Village from above", "Structures", "village top view paths houses map")
def sc_village_top():
    img = topdown_biome(1211, ["#7cbd4a", "#6cb544", "#5ea33a"], None, k=5)
    for (x0, y0, x1, y1) in ((4, 30, 60, 33), (30, 4, 33, 60), (12, 14, 33, 16)):
        rect(img, x0, y0, x1, y1, "#b08a5a")
    for (x, y) in ((14, 20), (20, 36), (38, 22), (40, 38), (8, 40), (44, 8), (22, 6), (50, 46)):
        rect(img, x, y, x + 6, y + 5, "#7a4a2a")
        rect(img, x, y, x + 6, y + 2, "#9a6a3a")
    rect(img, 29, 29, 34, 34, "#5a8ad8")                           # the well
    return img

@scene("stronghold_maze", "Stronghold corridors", "Structures", "stronghold maze corridors rooms library")
def sc_sh_maze():
    img = canvas("#3a4a2a")
    r = np.random.default_rng(1221)
    for _ in range(14):
        x, y = r.integers(4, 56), r.integers(4, 56)
        if r.random() < 0.5:
            rect(img, x - 12, y - 2, x + 12, y + 2, "#7a7a7a")
        else:
            rect(img, x - 2, y - 12, x + 2, y + 12, "#7a7a7a")
    for (x, y, w, hh) in ((8, 8, 12, 10), (40, 36, 14, 12), (36, 6, 10, 8)):
        rect(img, x, y, x + w, y + hh, "#8a8a8a")
        rect(img, x + 1, y + 1, x + w - 1, y + hh - 1, "#5a5a5a")
    rect(img, 16, 40, 28, 52, "#8a8a8a")
    rect(img, 18, 42, 26, 50, "#0a0a14")
    for k in range(4):
        rect(img, 19 + k * 2, 41, 20 + k * 2, 42, "#2a8a5a")
    return img

@scene("monument_top", "Monument from above", "Structures", "ocean monument top view guardians")
def sc_monument_top():
    h = -0.2 - 0.2 * fbm(N, 14, 1231)
    img = water(h, deep=0.5)
    rect(img, 12, 12, 52, 52, "#3a8a7a")
    rect(img, 16, 16, 48, 48, "#2a6a5e")
    rect(img, 24, 24, 40, 40, "#4aa08c")
    rect(img, 30, 30, 34, 34, "#c8f0e0")
    for (x, y) in ((14, 14), (46, 14), (14, 46), (46, 46)):
        rect(img, x, y, x + 4, y + 4, "#6ac0a8")
    return img

@scene("end_main_top", "Main End island", "End", "end island top view pillars dragon portal")
def sc_end_top():
    img = canvas("#05030a")
    stars(img, 30, 1241, N, "#8a7aa0")
    r, a = radial()
    wob = 1 + 0.08 * (fbm(N, 8, 1242) - 0.5)
    isl = r * wob < 27
    img[isl] = bands(fbm(N, 6, 1243), ["#c8c498", "#d8d4a8", "#e8e4b8"])[isl]
    for k in range(10):
        t = k / 10 * 2 * math.pi
        x, y = 32 + 19 * math.cos(t), 32 + 19 * math.sin(t)
        rect(img, x - 2, y - 2, x + 2, y + 2, "#1a1028")
        px(img, x, y, "#ff80ff")
    rect(img, 29, 29, 35, 35, "#3a3a3a")
    rect(img, 30, 30, 34, 34, "#0a0a14")
    return img

@scene("nether_top", "Nether from above", "Nether", "nether biomes map top view")
def sc_nether_top():
    v = fbm(N, 12, 1251)
    pal = ["#7a1a1a", "#a01a2a", "#1a8a7a", "#4a4a50", "#4a3a2a", "#7a1a1a"]
    img = bands(v, pal, dith=False)
    lava = fbm(N, 9, 1252) < 0.32
    img[lava] = C("#ff6a00")
    return land(v, img, sea=-1, shade_k=8)

@scene("mushroom_island_side", "Mushroom island (side)", "Landscapes", "mushroom island fields giant ocean")
def sc_mush_side():
    img = sky("#5fa8ff", "#d6ecff")
    rect(img, 0, 46, N, N, "#2b55c7")
    for x in range(0, N, 5):
        px(img, x, 46, "#7ab0f0")
    isl = ["......mmmmmmmmmmmmmmmmmm......", "...mmmmmmmmmmmmmmmmmmmmmmmm...", "dddddddddddddddddddddddddddddd"]
    stamp(img, isl, 32, 48, dict(m="#8b7790", d="#6a5050"), k=2)
    put(img, "mush_red", 20, 42, k=2)
    put(img, "mush_brown", 44, 42, k=2)
    return img

@scene("giant_mushrooms", "Giant mushrooms", "Landscapes", "giant huge mushrooms mycelium fields")
def sc_giant_mush():
    img = sky("#9a8ac8", "#f0d8e8")
    p = ground(img, 56, 14, 1261, grass="#8b7790", dirt="#6a5050")
    put(img, "mush_red", 18, p[18] - 1, k=4)
    put(img, "mush_brown", 48, p[48] - 1, k=3)
    return img

@scene("sinkhole_side", "Sinkhole (cut)", "Landscapes", "sinkhole hole cross section deep pit")
def sc_sink_side():
    img = sky("#6aa8f0", "#d0e8ff")
    rect(img, 0, 24, N, N, "#7a5534")
    rect(img, 0, 24, N, 26, "#5ea33a")
    x, y = grid()
    hole = (np.abs(x - 32) < 14 - (y - 24) * 0.08) & (y >= 24)
    depth = np.clip((y - 24) / 40, 0, 1)
    img[hole] = bands(depth, ["#3a3028", "#241e1a", "#141010", "#070605"])[hole]
    stone = (y > 34) & ~hole
    img[stone] = bands(fbm(N, 4, 1271), ["#5a5a5a", "#6a6a6a"])[stone]
    put(img, "oak", 8, 23)
    put(img, "oak", 56, 23)
    return img

@scene("mountain_ring_side", "Crown of peaks", "Landscapes", "mountain ring crown valley peaks circle")
def sc_ring_side():
    img = sky("#5a90e0", "#d8e8ff")
    ridge(img, 30, 8, 14, 1280, "#a8b8d0")                          # the far side of the ring
    n = noise1(N, 5, 1281)
    for xx in range(N):
        d = abs(xx - 32)
        top = int(58 - 46 * np.clip((d - 9) / 23, 0, 1) ** 0.7 - 5 * n[xx]) if d > 9 else 58
        img[top:, xx] = C("#6a7484")
        img[top:, xx][:, :] = C("#6a7484")
        if top < 34:
            img[top:min(top + 3 + xx % 3, N), xx] = C("#ffffff")
        if xx < 32:
            img[top:, xx] = shade(img[top:, xx], 0.85)
    rect(img, 22, 52, 42, N, "#5ea33a")
    rect(img, 26, 55, 38, 59, "#3f76e4")
    put(img, "spruce", 24, 51)
    put(img, "spruce", 40, 51)
    return img


@scene("badlands_side", "Badlands", "Landscapes", "badlands mesa terracotta layers red")
def sc_badlands_side():
    img = sky("#5fb0ff", "#ffe0b0")
    prof = ridge(img, 40, 22, 12, 1291, "#c2703d")
    x, y = grid()
    layers = ["#c2703d", "#e0b07a", "#a0522d", "#f0d2b0", "#b5651d", "#d9a066", "#8b4a2b"]
    for xx in range(N):
        for yy in range(max(0, prof[xx]), N):
            img[yy, xx] = C(layers[(yy // 3) % len(layers)])
    put(img, "cactus", 50, prof[50] - 1)
    return img

@scene("bamboo_jungle", "Bamboo jungle", "Landscapes", "bamboo jungle green tall")
def sc_bamboo():
    img = sky("#7ad0ff", "#e0ffe8")
    p = ground(img, 58, 10, 1301, grass="#3fb52c", dirt="#5a4a2a")
    r = np.random.default_rng(1302)
    for x in range(1, N, 3):
        hgt = r.integers(24, 50)
        for y in range(p[x] - hgt, p[x]):
            px(img, x, y, "#5aa83a" if y % 6 else "#3a7a2a")
        px(img, x + 1, p[x] - hgt, "#7ad04a")
        px(img, x - 1, p[x] - hgt + 2, "#7ad04a")
    return img

@scene("ice_spikes_side", "Ice spikes (side)", "Landscapes", "ice spikes tall frozen snow")
def sc_spikes_side():
    img = sky("#4a7ac0", "#c8e0ff")
    p = ground(img, 56, 12, 1311, grass="#ffffff", dirt="#c8d8e8")
    for (x, hgt, w) in ((14, 40, 3), (30, 28, 2), (46, 46, 3), (56, 20, 2)):
        for k in range(hgt):
            ww = max(0, int(w * (1 - k / hgt) + 0.5))
            rect(img, x - ww, p[x] - k, x + ww + 1, p[x] - k + 1, "#a9cff5" if k % 5 else "#d9ecff")
        px(img, x, p[x] - hgt, "#ffffff")
    return img

@scene("beach_sunset", "Beach at sunset", "Landscapes", "beach sunset ocean warm evening")
def sc_beach():
    img = sky("#3a2a6a", "#ffb070", cols=["#2a2060", "#5a3080", "#a04a80", "#e07070", "#ffb070", "#ffd8a0"])
    sun(img, 32, 36, 7, "#fff0b0", glow="#ffc080")
    rect(img, 0, 38, N, 50, "#2a4a8a")
    for y in range(39, 50, 2):
        rect(img, 28 - (y - 38), y, 36 + (y - 38), y + 1, "#ffd090")
    rect(img, 0, 50, N, N, "#e8c890")
    put(img, "palm", 52, 50, k=2)
    return img

@scene("savanna_side", "Savanna", "Landscapes", "savanna acacia dry plateau warm")
def sc_savanna_side():
    img = sky("#7ac0f0", "#fff0c0")
    ridge(img, 34, 8, 20, 1321, "#c8b070")
    p = ground(img, 56, 14, 1322, grass="#a8ad55", dirt="#8a6a3a")
    put(img, "acacia", 18, p[18] - 1, k=2)
    put(img, "acacia", 50, p[50] - 1)
    return img

@scene("pale_garden", "Pale garden", "Landscapes", "pale garden grey eerie creaking fog")
def sc_pale():
    img = sky("#8a8f96", "#d4d6d8")
    ridge(img, 40, 8, 14, 1331, "#a8aaa8")
    p = ground(img, 56, 12, 1332, grass="#9a9e98", dirt="#6a6660")
    for x in (10, 30, 52):
        stamp(img, ["..ggggg..", ".gggggggg", "ggggggggg", ".gg.t.gg.", "....t....", "....t....", "...ttt..."],
              x, p[x] - 1, dict(g="#c8ccc4", t="#5a5650"), k=2)
    rng = np.random.default_rng(1333)
    for _ in range(12):
        px(img, rng.integers(0, N), rng.integers(30, 60), "#f8f8f0")
    return img

@scene("waterfall", "Waterfall", "Landscapes", "waterfall cliff river mountains")
def sc_waterfall():
    img = sky("#6ab0f0", "#e0f0ff")
    rect(img, 0, 16, N, N, "#6a6a6a")
    img[16:, :] = bands(fbm(N, 5, 1341), ["#5a5a5a", "#6a6a6a", "#7a7a7a"])[16:]
    rect(img, 0, 16, N, 18, "#5ea33a")
    rect(img, 26, 16, 38, N, "#3f76e4")
    for y in range(16, N):
        for x in range(26, 38):
            if (x * 3 + y) % 7 == 0:
                px(img, x, y, "#9ac8ff")
    rect(img, 18, 54, 46, N, "#5b95f0")
    for x in range(18, 46, 2):
        px(img, x, 54, "#ffffff")
    put(img, "oak", 10, 15)
    put(img, "oak", 54, 15)
    return img

@scene("ravine", "Ravine", "Landscapes", "ravine canyon cut deep stone")
def sc_ravine():
    img = sky("#6aa8f0", "#d0e8ff")
    rect(img, 0, 20, N, N, "#6a6a6a")
    img[20:, :] = bands(fbm(N, 4, 1351), ["#5a5a5a", "#6a6a6a", "#7a7a7a"])[20:]
    rect(img, 0, 20, N, 22, "#5ea33a")
    rect(img, 0, 22, N, 26, "#7a5534")
    x, y = grid()
    cut = (np.abs(x - 32 - 3 * np.sin(y / 5)) < 7 - (y - 20) * 0.08) & (y >= 20)
    img[cut] = bands(np.clip((y - 20) / 44, 0, 1), ["#2a2420", "#181410", "#0a0806"])[cut]
    lava = cut & (y > 58)
    img[lava] = C("#ff6a00")
    return img

@scene("frozen_peaks", "Frozen peaks", "Landscapes", "frozen peaks ice packed snow cold")
def sc_frozen_peaks():
    img = sky("#2a4a8a", "#a8c8f0")
    ridge(img, 46, 36, 9, 1361, "#9ac0e8", jag=True, snow=(40, "#e8f4ff"))
    ridge(img, 58, 20, 7, 1362, "#7aa0d0", jag=True, snow=(46, "#ffffff"))
    return img

@scene("iceberg", "Iceberg", "Landscapes", "iceberg frozen ocean ice cold")
def sc_iceberg():
    img = sky("#5a8ad0", "#d8ecff")
    rect(img, 0, 40, N, N, "#2a4a8a")
    berg = ["......ww......", "....wwwwww....", "...wwwwwwwww..", "..wwwwwwwwwww.", ".wwwwwwwwwwwww"]
    stamp(img, berg, 30, 40, dict(w="#f0f8ff"), k=3)
    under = [".bbbbbbbbbbbbb", "..bbbbbbbbbbb.", "....bbbbbbb..."]
    stamp(img, under, 30, 49, dict(b="#6a9ad0"), k=3)
    return img

@scene("underwater_reef", "Coral reef (side)", "Landscapes", "coral reef underwater warm ocean fish")
def sc_reef_side():
    img = sky("#2aa8d8", "#0a4a8a", cols=["#46c0e0", "#2aa8d8", "#1f8ac0", "#1670a8", "#0f5a90", "#0a4a7a"])
    rect(img, 0, 56, N, N, "#e8d8a0")
    cols = ["#ff5a8a", "#ffd23f", "#6a5aff", "#ff8a3a", "#3ad8a0"]
    r = np.random.default_rng(1371)
    for x in range(2, N, 6):
        c = cols[r.integers(len(cols))]
        hgt = r.integers(6, 16)
        rect(img, x, 56 - hgt, x + 2, 56, c)
        rect(img, x - 1, 56 - hgt, x + 3, 56 - hgt + 2, c)
    for _ in range(4):
        x, y = r.integers(4, 56), r.integers(10, 40)
        rect(img, x, y, x + 3, y + 2, "#ffa040"); px(img, x - 1, y + 1, "#ffa040")
    return img

@scene("desert_well", "Desert well", "Structures", "desert well sand archaeology")
def sc_well():
    img = sky("#5fb0ff", "#fff0c8")
    p = ground(img, 54, 20, 1381, grass="#efe0a6", dirt="#d7c27a")
    w = ["sssssss", "s.....s", "s.....s", "sswwwss", "sssssss"]
    stamp(img, w, 32, p[32] - 1, dict(s="#d9c27a", w="#3f76e4"), k=3)
    rect(img, 23, p[32] - 18, 41, p[32] - 15, "#c8b070")
    return img

@scene("abandoned_camp", "Abandoned camp", "Structures", "camp tent abandoned fire")
def sc_camp():
    img = sky("#2a2a4a", "#7a6a8a")
    stars(img, 16, 1391, 24)
    p = ground(img, 54, 16, 1392, grass="#5a7a3a")
    tent = ["......c......", ".....ccc.....", "....ccccc....", "...ccc.ccc...", "..ccc...ccc..", ".ccc.....ccc."]
    stamp(img, tent, 24, p[24] - 1, dict(c="#a88a5a"), k=2)
    stamp(img, ["..o..", ".oyo.", "ooyoo", "wwwww"], 50, p[50] - 1, dict(o="#ff7a1a", y="#ffd060", w="#6a4a2a"), k=2)
    return img

@scene("village_night", "Village at night", "Structures", "village night lights houses")
def sc_village_night():
    img = sky("#0a1028", "#2a3a6a")
    stars(img, 30, 1401, 30)
    sun(img, 50, 10, 4, "#f0f0d0")
    p = ground(img, 58, 20, 1402, grass="#2a5a2a", dirt="#3a2a1a")
    stamp(img, ["..rrrr..", ".rrrrrr.", "rrrrrrrr", ".pwwwwp.", ".pwgwwp.", ".pwwdwp.", ".pwwdwp."], 18, p[18] - 1,
          dict(r="#3a2a1a", p="#2a1a10", w="#4a3a2a", g="#ffd060", d="#1a1008"), k=2)
    stamp(img, ["..rrrr..", ".rrrrrr.", "rrrrrrrr", ".pwwwwp.", ".pwwgwp.", ".pwwdwp.", ".pwwdwp."], 46, p[46] - 1,
          dict(r="#3a2a1a", p="#2a1a10", w="#4a3a2a", g="#ffd060", d="#1a1008"), k=2)
    return img

@scene("nether_portal", "Nether portal", "Structures", "nether portal obsidian purple gate")
def sc_nether_portal():
    img = sky("#4a6aa0", "#c0d0e8")
    p = ground(img, 60, 10, 1411, grass="#5ea33a")
    rect(img, 16, 8, 48, p[32], "#1a1028")
    rect(img, 20, 12, 44, p[32] - 4, "#6a2ab0")
    r = np.random.default_rng(1412)
    for _ in range(40):
        px(img, r.integers(20, 44), r.integers(12, p[32] - 4), ["#9a5ae0", "#4a1a8a"][r.integers(2)])
    return img

@scene("end_pillars", "End crystals", "End", "end pillars crystals cages dragon fight")
def sc_end_pillars():
    img = end_sky(1421)
    rect(img, 0, 58, N, N, "#e8e4b0")
    for (x, hgt, cage) in ((12, 34, True), (32, 44, False), (52, 28, True)):
        rect(img, x - 3, 58 - hgt, x + 3, 58, "#1a1028")
        rect(img, x - 3, 58 - hgt, x - 1, 58, "#2a1a40")
        rect(img, x - 2, 58 - hgt - 5, x + 3, 58 - hgt, "#ff80ff")
        px(img, x, 58 - hgt - 3, "#ffffff")
        if cage:
            for k in range(-4, 5, 2):
                rect(img, x + k, 58 - hgt - 8, x + k + 1, 58 - hgt, "#8a8a8a")
            rect(img, x - 4, 58 - hgt - 8, x + 5, 58 - hgt - 7, "#8a8a8a")
    return img

@scene("crimson_cave_glow", "Glowstone", "Nether", "glowstone light nether ceiling")
def sc_glowstone():
    img = nether_bg(1431, "#2a0808", "#5a1414", "#a03020")
    for (x, y, w) in ((14, 4, 8), (38, 2, 12), (54, 6, 6)):
        for k in range(w // 2 + 3):
            rect(img, x - w // 2 + k // 2, y + k, x + w // 2 - k // 2, y + k + 1, "#ffd060" if k % 2 else "#ffb030")
    ridge(img, 60, 10, 10, 1432, "#6a1a1a")
    return img

@scene("seed_map_grid", "Seed grid", "Abstract", "grid seeds many results list")
def sc_grid():
    img = canvas("#0e1222")
    r = np.random.default_rng(1441)
    for cy in range(2, N, 8):
        for cx in range(2, N, 8):
            c = mix(C("#3a5aff"), C("#ff5ac8"), r.random()) * (0.5 + 0.5 * r.random())
            rect(img, cx, cy, cx + 6, cy + 6, c)
    return img

@scene("octaves", "Noise octaves", "Abstract", "octaves noise perlin layers waves")
def sc_octaves():
    img = canvas("#0c1020")
    for k, col in enumerate(("#3ad0ff", "#7a8aff", "#c07aff", "#ff7ad0")):
        n = noise1(N, 32 / 2 ** k, 1451 + k, 1)
        for x in range(N):
            y = int(10 + k * 14 + (n[x] - 0.5) * 12)
            px(img, x, y, col)
            px(img, x, y + 1, mix(C(col), C("#0c1020"), 0.5))
    return img

@scene("aurora", "Aurora", "Abstract", "aurora northern lights night sky cold")
def sc_aurora():
    img = sky("#04081a", "#0a1a3a")
    stars(img, 40, 1461, N)
    x, y = grid()
    for k, col in enumerate(("#3affb0", "#3ad0ff", "#a07aff")):
        c = 20 + k * 6 + 6 * np.sin(x / 7 + k)
        band = np.clip(1 - np.abs(y - c) / 4, 0, 1)
        img = img * (1 - band[..., None] * 0.6) + C(col) * band[..., None] * 0.6
    ridge(img, 58, 8, 12, 1462, "#05070f")
    return img


# ── write ────────────────────────────────────────────────────────────────────
def render_all():
    os.makedirs(OUT, exist_ok=True)
    index, pics = [], []
    for key, title, cat, tags, fn in SCENES:
        img = np.clip(fn(), 0, 255).astype(np.uint8)
        im = Image.fromarray(img, "RGB").resize((N * 4, N * 4), Image.NEAREST)
        im.save(os.path.join(OUT, key + ".png"), optimize=True)
        index.append(dict(key=key, title=title, category=cat, tags=tags))
        pics.append((title, im))
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1)
    return pics


def sheet(pics, path):
    cols = 10
    rows = (len(pics) + cols - 1) // cols
    s = Image.new("RGB", (cols * 132, rows * 132), (20, 22, 30))
    for i, (_t, im) in enumerate(pics):
        s.paste(im.resize((128, 128), Image.NEAREST), ((i % cols) * 132 + 2, (i // cols) * 132 + 2))
    s.save(path)


if __name__ == "__main__":
    pics = render_all()
    print(len(pics), "pictures in", OUT)
    if "sheet" in sys.argv[1:]:
        sheet(pics, os.path.join(OUT, "..", "..", "_session_pictures_sheet.png"))
