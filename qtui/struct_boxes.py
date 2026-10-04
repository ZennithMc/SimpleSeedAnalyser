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

  footprint(key, x, z, seed) -> (x0, z0, x1, z1, exact) | None   (blocks, inclusive)
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


def footprint(key, x, z, seed):
    cx, cz = int(x) >> 4, int(z) >> 4
    ox, oz = cx << 4, cz << 4
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
