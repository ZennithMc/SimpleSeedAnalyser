"""
biome_climate.py — the exact climate requirements of the 1.18+ overworld biomes
(19/10), from the game's own parameter points (OverworldBiomeBuilder, the
leaves of cubiomes' climate tree; ×10000 like every SSA climate condition).

How the game picks a biome: the 6 parameters of a spot (temperature,
humidity, continentalness, erosion, depth, weirdness) form a point; each
biome owns a list of boxes in that 6-D space; the biome whose box is the
NEAREST to the point wins (distance 0 inside a box). The surface boxes tile
the space, so in practice: inside a box of the biome → that biome.

    boxes(mc, biome)  -> merged boxes [(lo[6], hi[6])]   (params in PARAMS order)
    hull(mc, biome)   -> (lo[6], hi[6]) over all of them
    fmt_range(k, lo, hi), contains(box, np)
"""
from __future__ import annotations

PARAMS = ("T", "H", "C", "E", "D", "W")
PARAM_NAMES = ("Temperature", "Humidity", "Continentalness", "Erosion", "Depth", "Weirdness")
_CACHE = {}


def _lib():
    import cubiomes_wrap as cw
    lib = _CACHE.get("lib")
    if lib is None:
        lib = _CACHE["lib"] = cw.CubiomesLib()
    return lib


def raw_boxes(mc, biome):
    k = ("raw", int(mc), int(biome))
    got = _CACHE.get(k)
    if got is None:
        got = _CACHE[k] = _lib().biome_param_boxes(mc, biome)
    return got


def _merge_axis(boxes, k):
    """Merge boxes equal on every parameter but k whose k-intervals touch."""
    groups = {}
    for lo, hi in boxes:
        key = tuple(lo[i] for i in range(6) if i != k) + tuple(hi[i] for i in range(6) if i != k)
        groups.setdefault(key, []).append((lo, hi))
    out, changed = [], False
    for g in groups.values():
        g.sort(key=lambda b: b[0][k])
        cur_lo, cur_hi = list(g[0][0]), list(g[0][1])
        for lo, hi in g[1:]:
            if lo[k] <= cur_hi[k] + 1 or (k == 4 and cur_hi[k] == 0 and lo[k] == 10000):
                # (depth: the game gives the surface points twice, at 0 and at 1)
                cur_hi[k] = max(cur_hi[k], hi[k])
                changed = True
            else:
                out.append((tuple(cur_lo), tuple(cur_hi)))
                cur_lo, cur_hi = list(lo), list(hi)
        out.append((tuple(cur_lo), tuple(cur_hi)))
    return out, changed


def boxes(mc, biome):
    k = ("boxes", int(mc), int(biome))
    got = _CACHE.get(k)
    if got is not None:
        return got
    bx = []
    seen = set()
    for r in raw_boxes(mc, biome):
        lo = tuple(r[0::2]); hi = tuple(r[1::2])
        if (lo, hi) not in seen:
            seen.add((lo, hi)); bx.append((lo, hi))
    for _ in range(12):                                   # until stable
        any_c = False
        for axis in (4, 5, 3, 2, 1, 0):
            bx, c = _merge_axis(bx, axis)
            any_c |= c
        if not any_c:
            break
    bx.sort(key=lambda b: (b[0][4], b[0][0], b[0][1], b[0][2], b[0][3], b[0][5]))
    _CACHE[k] = bx
    return bx


def hull(mc, biome):
    bx = boxes(mc, biome)
    if not bx:
        return None
    return (tuple(min(b[0][i] for b in bx) for i in range(6)),
            tuple(max(b[1][i] for b in bx) for i in range(6)))


def contains(box, np6):
    lo, hi = box
    return all(lo[i] <= np6[i] <= hi[i] for i in range(6))


def distance2(box, np6):
    lo, hi = box
    d = 0
    for i in range(6):
        v = np6[i]
        e = lo[i] - v if v < lo[i] else (v - hi[i] if v > hi[i] else 0)
        d += e * e
    return d


def fmt_range(k, lo, hi):
    """'any', 'surface', '≥ 3000', '≤ -4500', '-1500 … 2000' (×10000)."""
    if k == 4:                                            # depth
        if lo == 0 and hi == 10000:
            return "surface"
        if lo == hi:
            return f"= {lo}"
    if lo <= -10000 and hi >= 10000:
        return "any"
    if lo <= -10000:
        return f"≤ {hi}"
    if hi >= 10000:
        return f"≥ {lo}"
    return f"{lo} … {hi}"


# ── (19/10) a readable form: climate groups × terrain alternatives ──────────
# the game's own level boundaries (OverworldBiomeBuilder), ×10000
LEVELS = {
    0: [(-10000, -4500, "Frozen"), (-4500, -1500, "Cold"), (-1500, 2000, "Temperate"),
        (2000, 5500, "Warm"), (5500, 10000, "Hot")],
    1: [(-10000, -3500, "Arid"), (-3500, -1000, "Dry"), (-1000, 1000, "Neutral"),
        (1000, 3000, "Wet"), (3000, 10000, "Humid")],
    2: [(-12000, -10500, "Mushroom islands"), (-10500, -4550, "Deep ocean"), (-4550, -1900, "Ocean"),
        (-1900, -1100, "Coast"), (-1100, 300, "Near inland"), (300, 3000, "Mid inland"),
        (3000, 10000, "Far inland")],
    3: [(-10000, -7800, "E0"), (-7800, -3750, "E1"), (-3750, -2225, "E2"), (-2225, 500, "E3"),
        (500, 4500, "E4"), (4500, 5500, "E5"), (5500, 10000, "E6")],
    5: [(-10000, -9333, "Mid"), (-9333, -7666, "High"), (-7666, -5666, "Peaks"),
        (-5666, -4000, "High"), (-4000, -2666, "Mid"), (-2666, -500, "Low"), (-500, 500, "Valleys"),
        (500, 2666, "Low"), (2666, 4000, "Mid"), (4000, 5666, "High"), (5666, 7666, "Peaks"),
        (7666, 9333, "High"), (9333, 10000, "Mid")],
}
LEVEL_HINT = {
    0: "Temperature",
    1: "Humidity",
    2: "Continentalness — how far from the ocean",
    3: "Erosion — E0 = most mountainous … E6 = flattest",
    5: "Weirdness — the terrain shape (valleys, low, mid, high, peaks); "
       "its sign picks the variant biomes",
    4: "Depth — below the surface (cave biomes)",
}


def level_names(k, lo, hi):
    """Names of the game's levels an interval covers (in order, no repeat in a row)."""
    if k == 4:
        return ["Surface"] if (lo, hi) == (0, 10000) else ["Underground"]
    out = []
    for a, b, name in LEVELS.get(k, []):
        if min(hi, b) - max(lo, a) > 2:           # overlaps (± the tables' 1-unit edges)
            if not out or out[-1] != name:
                out.append(name)
    if k == 5 and out:
        order = ["Valleys", "Low", "Mid", "High", "Peaks"]
        uniq = [n for n in order if n in out]
        side = " (W > 0)" if lo >= -2 else " (W < 0)" if hi <= 2 else ""
        if len(uniq) == 5:
            return ["Any shape" + side]
        if len(uniq) == 4:
            miss = next(n for n in order if n not in uniq)
            return [f"Any but {miss}" + side]
        return [" & ".join(uniq) + side]
    if k in (0, 1, 2, 3) and len(out) > 2:
        return [f"{out[0]} → {out[-1]}"]
    return [" · ".join(out)] if out else ["—"]


def level_of(k, v):
    """The game's level name of one value (the spot strip)."""
    if k == 4:
        return "Surface" if 0 <= v <= 10000 else "Underground"
    for a, b, name in LEVELS.get(k, []):
        if a <= v < b:
            if k == 5:
                return f"{name} (W {'>' if v >= 0 else '<'} 0)"
            return name
    lv = LEVELS.get(k)
    return (lv[0][2] if v < lv[0][0] else lv[-1][2]) if lv else "—"


def hull_name(k, lo, hi):
    """Plain name of a biome's envelope on one parameter (the CV-style view)."""
    if k == 4:
        return "Surface" if (lo, hi) == (0, 10000) else "Underground"
    if fmt_range(k, lo, hi) == "any":
        return "Any"
    if k == 5:
        if -500 <= lo <= 500 and hi >= 10000:
            return "W > 0 side" + (" (+ valleys)" if lo < 0 else "")
        if lo <= -10000 and -500 <= hi <= 500:
            return "W < 0 side" + (" (+ valleys)" if hi > 0 else "")
    n = level_names(k, lo, hi)[0]
    return n


def _merge_list(iv):
    iv = sorted(iv)
    out = []
    for a, b in iv:
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return tuple(out)


def grouped(mc, biome):
    """[{climates: [(T, H, D) intervals], terrains: [{2: [C…], 3: [E…], 5: [W…]}]}]
    — exactly the union of the boxes: a spot gets the biome (inside a box) iff
    it fits ONE climate of a group AND one terrain of that same group."""
    bx = boxes(mc, biome)
    by_clim = {}
    for lo, hi in bx:
        key = ((lo[0], hi[0]), (lo[1], hi[1]), (lo[4], hi[4]))
        by_clim.setdefault(key, []).append({2: ((lo[2], hi[2]),), 3: ((lo[3], hi[3]),),
                                            5: ((lo[5], hi[5]),)})
    groups = {}
    for clim, rows in by_clim.items():
        changed = True
        while changed:                              # rows equal but in one parameter: union
            changed = False
            for i in range(len(rows)):
                for j in range(i + 1, len(rows)):
                    a, b = rows[i], rows[j]
                    diff = [k for k in (2, 3, 5) if a[k] != b[k]]
                    if len(diff) == 1:
                        k = diff[0]
                        a[k] = _merge_list(list(a[k]) + list(b[k]))
                        rows.pop(j); changed = True
                        break
                if changed:
                    break
        sig = tuple(sorted(tuple(r[k] for k in (2, 3, 5)) for r in rows))
        g = groups.setdefault(sig, {"climates": [], "terrains": rows})
        g["climates"].append(clim)
    out = list(groups.values())
    for g in out:
        g["climates"].sort()
        g["terrains"].sort(key=lambda r: (r[2][0], r[3][0], r[5][0]))
    out.sort(key=lambda g: g["climates"][0])
    return out


def names_list(k, ivs):
    """Level names of a union of intervals ('or' between disjoint ones)."""
    if len(ivs) == 1 and fmt_range(k, *ivs[0]) == "any":
        return "Any"
    out = []
    for a, b in ivs:
        n = level_names(k, a, b)[0]
        if n not in out:
            out.append(n)
    return "  or  ".join(out)


def fmt_list(k, ivs):
    return "  or  ".join(fmt_range(k, a, b) for a, b in ivs)


def fits(ivs, v):
    return any(a <= v <= b for a, b in ivs)
