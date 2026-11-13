"""
iceberg.py — (30/10) IcebergFeature (1.18+): the packed / blue ice icebergs of the
frozen oceans (LOCAL_MODIFICATIONS: iceberg_packed rarity 1/16, iceberg_blue 1/200,
in_square, biome filter at the chunk's minimum Y), on the terrain after the carvers.

They matter for the heightmaps of a chunk saved and loaded again before its
structures (its WG heightmaps are primed again from its blocks): a shipwreck next to
an iceberg is then set higher.
"""
from __future__ import annotations

import math

import numpy as np

AIR, WATER = 1, 2
PICE, SNOWB = 40, 41                 # packed / blue ice (the iceberg's block), snow block
_ICE_STATES = (PICE, SNOWB)
_FROZEN = (10, 50)                   # cubiomes: frozen_ocean, deep_frozen_ocean
_INDEX = {"packed": 0, "blue": 1}    # LOCAL_MODIFICATIONS: iceberg_packed, iceberg_blue, amethyst_geode
_RARITY = {"packed": 16, "blue": 200}
SEA = 63

f32 = np.float32
np.seterr(divide="ignore", invalid="ignore", over="ignore")


def _ceil(v):
    return int(math.ceil(float(v)))


def _nint(r, n):
    import mineshaft118 as ms
    return ms._nint(r, n)


def _ndouble(r):
    return ((r.next(26) << 27) + r.next(27)) * (1.0 / (1 << 53))


def _nbool(r):
    return r.next(1) != 0


def _nfloat(r):
    return f32(r.next(24) * (1.0 / (1 << 24)))


def _jdiv(a, b):
    """Java double division (b may be 0)."""
    if b:
        return a / b
    if a == 0 or a != a:
        return float("nan")
    return math.copysign(float("inf"), a) * (math.copysign(1.0, float(b)))


class _Ice:
    def __init__(self, W, r):
        self.W, self.r = W, r

    # ── shapes ──
    def radius_round(self, yo, h, w):
        r = self.r
        k = f32(3.5) - _nfloat(r)
        scale = (f32(1.0) - f32(float(yo) ** 2.0) / (f32(h) * k)) * f32(w)
        if h > 15 + _nint(r, 5):
            t = yo // 2 if yo < 3 + _nint(r, 6) else yo
            scale = (f32(1.0) - f32(t) / (f32(h) * k * f32(0.4))) * f32(w)
        return _ceil(scale / f32(2.0))

    @staticmethod
    def radius_ellipse(yo, h, w):
        scale = (f32(1.0) - f32(float(yo) ** 2.0) / (f32(h) * f32(1.0))) * f32(w)
        return _ceil(scale / f32(2.0))

    def radius_steep(self, yo, h, w):
        k = f32(1.0) + _nfloat(self.r) / f32(2.0)
        scale = (f32(1.0) - f32(yo) / (f32(h) * k)) * f32(w)
        return _ceil(scale / f32(2.0))

    def dist_circle(self, xo, zo, ox, oz, radius):
        off = f32(10.0) * min(max(_nfloat(self.r), f32(0.2)), f32(0.8)) / f32(radius)
        return float(off) + float(xo - ox) ** 2.0 + float(zo - oz) ** 2.0 - float(radius) ** 2.0

    @staticmethod
    def dist_ellipse(xo, zo, ox, oz, a, c, ang):
        dx, dz = float(xo - ox), float(zo - oz)
        return _jdiv(dx * math.cos(ang) - dz * math.sin(ang), a) ** 2.0 \
            + _jdiv(dx * math.sin(ang) + dz * math.cos(ang), c) ** 2.0 - 1.0

    @staticmethod
    def ell_c(yo, h, c):
        if yo > 0 and h - yo <= 3:
            c -= 4 - (h - yo)
        return c

    # ── blocks ──
    def set_block(self, X, Y, Z, h_diff, h, ell, snow, main):
        st = self.W.get(X, Y, Z)
        if st in (AIR, SNOWB, WATER):                # air, snow block, ice, water
            rnd = (not ell) or _ndouble(self.r) > 0.05
            div = 3 if ell else 2
            if snow and st != WATER and float(h_diff) <= float(_nint(self.r, max(1, h // div))) + h * 0.6 and rnd:
                self.W.set(X, Y, Z, SNOWB)
            else:
                self.W.set(X, Y, Z, main)

    def gen_block(self, o, h, xo, yo, zo, radius, a, ell, ec, ang, snow, main):
        if ell:
            d = self.dist_ellipse(xo, zo, 0, 0, a, self.ell_c(yo, h, ec), ang)
        else:
            d = self.dist_circle(xo, zo, 0, 0, radius)
        if d < 0.0:
            cmp = -0.5 if ell else float(-6 - _nint(self.r, 3))
            if d > cmp and _ndouble(self.r) > 0.9:
                return
            self.set_block(o[0] + xo, o[1] + yo, o[2] + zo, h - yo, h, ell, snow, main)

    def smooth(self, o, w, h, ell, ea):
        W = self.W
        a = ea if ell else w // 2
        for x in range(-a, a + 1):
            for z in range(-a, a + 1):
                for yo in range(0, h + 1):
                    X, Y, Z = o[0] + x, o[1] + yo, o[2] + z
                    st = W.get(X, Y, Z)
                    if st not in _ICE_STATES:          # (+ snow layers: none here)
                        continue
                    if W.get(X, Y - 1, Z) == AIR:
                        W.set(X, Y, Z, AIR)
                        W.set(X, Y + 1, Z, AIR)
                        continue
                    n = sum(1 for (dx, dz) in ((-1, 0), (1, 0), (0, -1), (0, 1))
                            if W.get(X + dx, Y, Z + dz) not in _ICE_STATES)
                    if n >= 3:
                        W.set(X, Y, Z, AIR)

    def carve(self, radius, yo, o, under, ang, lo, ea, ec):
        W = self.W
        a = radius + 1 + ea // 3
        c = min(radius - 3, 3) + ec // 2 - 1
        for xo in range(-a, a):
            for zo in range(-a, a):
                d = self.dist_ellipse(xo, zo, lo[0], lo[2], a, c, ang)
                if not d < 0.0:
                    continue
                X, Y, Z = o[0] + xo, o[1] + yo, o[2] + zo
                if W.get(X, Y, Z) not in _ICE_STATES:
                    continue
                W.set(X, Y, Z, WATER if under else AIR)

    def cut_out(self, w, h, o, ell, ea, ang, ec):
        r = self.r
        sx = -1 if _nbool(r) else 1
        sz = -1 if _nbool(r) else 1
        xoff = _nint(r, max(w // 2 - 2, 1))
        if _nbool(r):
            xoff = w // 2 + 1 - _nint(r, max(w - w // 2 - 1, 1))
        zoff = _nint(r, max(w // 2 - 2, 1))
        if _nbool(r):
            zoff = w // 2 + 1 - _nint(r, max(w - w // 2 - 1, 1))
        if ell:
            xoff = zoff = _nint(r, max(ea - 5, 1))
        lo = (sx * xoff, 0, sz * zoff)
        angle = ang + 1.5707963267948966 if ell else _ndouble(r) * 2.0 * math.pi
        for yo in range(0, h - 3):
            rad = self.radius_round(yo, h, w)
            self.carve(rad, yo, o, False, angle, lo, ea, ec)
        yo = -1
        while yo > -h + _nint(r, 5):
            rad = self.radius_steep(-yo, h, w)
            self.carve(rad, yo, o, True, angle, lo, ea, ec)
            yo -= 1

    def place(self, x, z, main):
        r = self.r
        o = (x, SEA, z)
        snow = _ndouble(r) > 0.7
        ang = _ndouble(r) * 2.0 * math.pi
        ea = 11 - _nint(r, 5)
        ec = 3 + _nint(r, 3)
        ell = _ndouble(r) > 0.7
        over = _nint(r, 6) + 6 if ell else _nint(r, 15) + 3
        if not ell and _ndouble(r) > 0.9:
            over += _nint(r, 19) + 7
        under = min(over + _nint(r, 11), 18)
        w = min(over + _nint(r, 7) - _nint(r, 5), 11)
        a = ea if ell else 11
        for xo in range(-a, a):
            for zo in range(-a, a):
                for yo in range(0, over):
                    rad = self.radius_ellipse(yo, over, w) if ell else self.radius_round(yo, over, w)
                    if not ell and xo >= rad:
                        continue
                    self.gen_block(o, over, xo, yo, zo, rad, a, ell, ec, ang, snow, main)
        self.smooth(o, w, over, ell, ea)
        for xo in range(-a, a):
            for zo in range(-a, a):
                yo = -1
                while yo > -under:
                    na = _ceil(f32(a) * (f32(1.0) - f32(float(yo) ** 2.0) / (f32(under) * f32(8.0)))) \
                        if ell else a
                    rad = self.radius_steep(-yo, under, w)
                    if xo < rad:
                        self.gen_block(o, under, xo, yo, zo, rad, na, ell, ec, ang, snow, main)
                    yo -= 1
        cut = _ndouble(r) > 0.1 if ell else _ndouble(r) > 0.7
        if cut:
            self.cut_out(w, over, o, ell, ea, ang, ec)


class _Rec:
    def __init__(self, R):
        self.R, self.w = R, {}

    def get(self, x, y, z):
        v = self.w.get((x, y, z))
        return self.R.get(x, y, z) if v is None else v

    def set(self, x, y, z, v):
        self.w[(x, y, z)] = v


_CACHE = {}


def chunk_icebergs(seed, mc, cx, cz, biome=None):
    """{(x, y, z): code} written by the icebergs of chunk (cx, cz), on the terrain
    alone (cached)."""
    import structure_loot as sl
    import prefeatures118 as pf
    k = (int(seed), int(mc), cx, cz)
    v = _CACHE.get(k)
    if v is not None:
        return v
    if biome is None:
        import struct_info
        lib = struct_info._lib(int(seed), int(mc), None, 0)

        def biome(x, y, z):
            return int(lib.get_biome_at(x, z, scale=4, y=y))
    rec = None
    for kind in ("packed", "blue"):
        r = sl.structure_rng(seed, mc, cx, cz, _INDEX[kind], 2)
        if not r.next_float() < float(f32(1.0) / f32(_RARITY[kind])):
            continue
        x = _nint(r, 16) + (cx << 4)
        z = _nint(r, 16) + (cz << 4)
        if biome(x, -64, z) not in _FROZEN:
            continue
        if rec is None:
            rec = _Rec(pf.Region(int(seed), mc, []))
        _Ice(rec, r).place(x, z, PICE)
    v = {} if rec is None else rec.w
    if len(_CACHE) > 2048:
        _CACHE.clear()
    _CACHE[k] = v
    return v
