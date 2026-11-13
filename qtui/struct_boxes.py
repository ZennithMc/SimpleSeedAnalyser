"""
qtui/struct_boxes.py — the real size of the structures on the map (03/10).

When the map is zoomed in, each structure gets its footprint (like
cubiomes-viewer):
  * EXACT for the fixed-size pieces: desert pyramid (21 × 21), jungle temple
    (12 × 15) and swamp hut (7 × 9) — oriented with the very draw Minecraft
    makes (ScatteredFeaturePiece: setLargeFeatureSeed, nextInt(4) over N E S W);
    the ocean monument (58 × 58 around its chunk) and buried treasure (1 block);
  * APPROXIMATE (dashed) for the generated ones (villages, outposts, mansions,
    shipwrecks, ruins, ruined portals, ancient cities, trial chambers…): a box of
    their usual size, not their real pieces.

  * (24/10) EXACT too: shipwrecks (their template, rotation and beached / ocean
    variant, as scattered_loot draws them), ocean ruins (the big ruin and its
    cluster of small ones: a LIST of boxes) and igloos (the dome, igloo/top 7 × 8
    rotated around (3, 5)).

  footprint(key, x, z, seed, mc) -> (x0, z0, x1, z1, exact) | None   (blocks, inclusive)
"""
from __future__ import annotations

from functools import lru_cache

# (dx0, dz0, dx1, dz1) from the chunk origin, for the approximate ones
_APPROX = {
    "village": (-40, -40, 56, 56),
    "pillager_outpost": (-12, -12, 28, 28),
    "mansion": (-25, -25, 39, 39),
    "igloo": (0, 0, 7, 8),
    "shipwreck": (0, 0, 15, 23),
    "ocean_ruin": (0, 0, 15, 15),
    "ruined_portal": (0, 0, 13, 13),
    "ancient_city": (-48, -48, 63, 63),
    "trail_ruins": (-8, -8, 23, 23),
    "trial_chambers": (-32, -32, 47, 47),
    "abandoned_camp": (0, 0, 15, 15),
}
# fixed pieces: (width, depth) of ScatteredFeaturePiece
_SCATTERED = {"desert_pyramid": (21, 21), "jungle_temple": (12, 15), "swamp_hut": (7, 9)}


@lru_cache(maxsize=4096)
def _direction(seed, cx, cz):
    """Direction.Plane.HORIZONTAL.getRandomDirection(random) of the structure start
    random: 0 N, 1 E, 2 S, 3 W."""
    import stronghold_eyes as se
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    return r.nextInt_bound(4)


@lru_cache(maxsize=8192)
def _ship(seed, mc, cx, cz):
    try:
        import scattered_loot as sc
        return sc.shipwreck_box(int(seed), int(mc), cx, cz)[0]
    except Exception:
        return None


@lru_cache(maxsize=8192)
def _ruins(seed, mc, cx, cz):
    try:
        import scattered_loot as sc
        return tuple(sc.ocean_ruin_boxes(int(seed), int(mc), cx, cz))
    except Exception:
        return None


def footprint(key, x, z, seed, mc=None):
    cx, cz = int(x) >> 4, int(z) >> 4
    ox, oz = cx << 4, cz << 4
    if key == "shipwreck" and mc is not None and int(mc) >= 19:
        b = _ship(seed, int(mc), cx, cz)
        if b is not None:
            return b[0], b[1], b[2], b[3], True
    if key == "ocean_ruin" and mc is not None and int(mc) >= 19:
        parts = _ruins(seed, int(mc), cx, cz)
        if parts:
            return [(b[0], b[1], b[2], b[3], True) for b in parts]
    if key == "igloo":
        import scattered_loot as sc
        rot = sc.igloo_layout(seed, cx, cz)[0]
        pts = [sc._rot(a, b, rot, 3, 5) for a in (0, 6) for b in (0, 7)]
        xs = [ox + a for a, _ in pts]
        zs = [oz + b for _, b in pts]
        return min(xs), min(zs), max(xs), max(zs), True
    if key in _SCATTERED:
        w, d = _SCATTERED[key]
        if w != d and _direction(seed, cx, cz) in (1, 3):     # east / west: rotated
            w, d = d, w
        return ox, oz, ox + w - 1, oz + d - 1, True
    if key == "monument":
        return ox - 29, oz - 29, ox + 28, oz + 28, True
    if key == "buried_treasure":
        return ox + 9, oz + 9, ox + 9, oz + 9, True
    a = _APPROX.get(key)
    if a is None:
        return None
    return ox + a[0], oz + a[1], ox + a[2], oz + a[3], False


# ── stronghold pieces: colour by height ───────────────────────────────────────
# from the lowest piece of the stronghold (deep red) to its highest (cyan)
_GRAD = [(0.0, (150, 20, 30)), (0.2, (220, 50, 40)), (0.4, (240, 120, 40)),
         (0.6, (240, 200, 60)), (0.8, (120, 210, 80)), (1.0, (60, 200, 230))]


def grad_rgb(t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    for (t0, c0), (t1, c1) in zip(_GRAD, _GRAD[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(int(a + (b - a) * f) for a, b in zip(c0, c1))
    return _GRAD[-1][1]
