"""
surface_ice.py — (30/10) SurfaceSystem.frozenOceanExtension (1.18+): the packed ice /
snow pillars the SURFACE step puts in the frozen and deep frozen oceans (noises
iceberg_surface, iceberg_pillar, iceberg_pillar_roof), before the carvers — so they
are in the WG heightmaps (shipwrecks, ocean ruins), unlike the iceberg features.
"""
from __future__ import annotations

import hashlib
import math
import struct

M64 = (1 << 64) - 1
SILVER, GOLDEN = 0x6A09E667F3BCC909, 0x9E3779B97F4A7C15
PICE, SNOWB = 40, 41
AIR, WATER = 1, 2
SEA = 63
FROZEN_OCEAN, DEEP_FROZEN_OCEAN = 10, 50          # cubiomes ids


def _s64(v):
    v &= M64
    return v - (1 << 64) if v >> 63 else v


def _rotl(x, k):
    return ((x << k) | (x >> (64 - k))) & M64


def _mix13(z):
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & M64
    return z ^ (z >> 31)


class Xoro:
    """XoroshiroRandomSource (raw: its own nextInt / nextDouble)."""
    __slots__ = ("lo", "hi")

    def __init__(self, lo, hi):
        self.lo, self.hi = lo & M64, hi & M64
        if (self.lo | self.hi) == 0:
            self.lo, self.hi = GOLDEN, SILVER

    @classmethod
    def from_seed(cls, seed):
        lo = (int(seed) & M64) ^ SILVER
        return cls(_mix13(lo), _mix13((lo + GOLDEN) & M64))

    def next_long(self):
        lo, hi = self.lo, self.hi
        r = (_rotl((lo + hi) & M64, 17) + lo) & M64
        hi ^= lo
        self.lo = _rotl(lo, 49) ^ hi ^ ((hi << 21) & M64)
        self.hi = _rotl(hi, 28)
        return r

    def next_int32(self):
        v = self.next_long() & 0xFFFFFFFF
        return v - (1 << 32) if v >> 31 else v

    def next_int(self, bound):
        l = self.next_long() & 0xFFFFFFFF
        m = l * bound
        n = m & 0xFFFFFFFF
        if n < bound:
            i = ((~bound + 1) & 0xFFFFFFFF) % bound
            while n < i:
                l = self.next_long() & 0xFFFFFFFF
                m = l * bound
                n = m & 0xFFFFFFFF
        return m >> 32

    def next_double(self):
        return (self.next_long() >> 11) * 1.1102230246251565E-16

    def fork_positional(self):
        return Positional(self.next_long(), self.next_long())


class Positional:
    __slots__ = ("lo", "hi")

    def __init__(self, lo, hi):
        self.lo, self.hi = lo & M64, hi & M64

    def from_hash(self, s):
        d = hashlib.md5(s.encode("utf-8")).digest()
        a, b = struct.unpack(">QQ", d)
        return Xoro(a ^ self.lo, b ^ self.hi)

    def at(self, x, y, z):
        import ocean_ruin_y as oy
        return Xoro((oy.mth_seed(x, y, z) & M64) ^ self.lo, self.hi)


def _wrap(d):
    return d - math.floor(d / 3.3554432E7 + 0.5) * 3.3554432E7


class _Perlin:
    def __init__(self, r, first, amps):
        import prefeatures118 as pf
        pf_ = r.fork_positional()
        self.levels = [pf._Improved(pf_.from_hash(f"octave_{first + k}")) if a != 0 else None
                       for k, a in enumerate(amps)]
        self.amps = amps
        self.f_in = 2.0 ** first
        n = len(amps)
        self.f_val = 2.0 ** (n - 1) / (2.0 ** n - 1.0)

    def value(self, x, y, z):
        d, e, f = 0.0, self.f_in, self.f_val
        for a, lv in zip(self.amps, self.levels):
            if lv is not None:
                d += a * lv.noise(_wrap(x * e), _wrap(y * e), _wrap(z * e)) * f
            e *= 2.0
            f /= 2.0
        return d


class Normal:
    def __init__(self, r, first, amps):
        self.a = _Perlin(r, first, amps)
        self.b = _Perlin(r, first, amps)
        nz = [i for i, v in enumerate(amps) if v != 0]
        oc = nz[-1] - nz[0]
        self.k = 0.16666666666666666 / (0.1 * (1.0 + 1.0 / (oc + 1)))

    def value(self, x, y, z):
        q = 1.0181268882175227
        return (self.a.value(x, y, z) + self.b.value(x * q, y * q, z * q)) * self.k


# ── PerlinSimplexNoise (Biome's FROZEN_TEMPERATURE_NOISE, BIOME_INFO_NOISE: fixed seeds) ──
_F2 = 0.5 * (math.sqrt(3.0) - 1.0)
_G2 = (3.0 - math.sqrt(3.0)) / 6.0
_GRAD = ((1, 1, 0), (-1, 1, 0), (1, -1, 0), (-1, -1, 0), (1, 0, 1), (-1, 0, 1), (1, 0, -1), (-1, 0, -1),
         (0, 1, 1), (0, -1, 1), (0, 1, -1), (0, -1, -1), (1, 1, 0), (0, -1, 1), (-1, 1, 0), (0, -1, -1))


class _Simplex:
    def __init__(self, r):
        self.xo = r.next_double() * 256.0
        self.yo = r.next_double() * 256.0
        self.zo = r.next_double() * 256.0
        p = list(range(256))
        for i in range(256):
            j = r.next_int(256 - i)
            p[i], p[i + j] = p[i + j], p[i]
        self.p = p

    def _c(self, i, x, y, base):
        d = base - x * x - y * y
        if d < 0.0:
            return 0.0
        d *= d
        g = _GRAD[i]
        return d * d * (g[0] * x + g[1] * y)

    def value(self, x, y):
        P = self.p
        d = (x + y) * _F2
        i, j = math.floor(x + d), math.floor(y + d)
        e = (i + j) * _G2
        h, k = x - (i - e), y - (j - e)
        l, m = (1, 0) if h > k else (0, 1)
        n, o = h - l + _G2, k - m + _G2
        q, r = h - 1.0 + 2.0 * _G2, k - 1.0 + 2.0 * _G2
        s, t = i & 255, j & 255
        u = P[(s + P[t]) & 255] % 12
        v = P[(s + l + P[(t + m) & 255]) & 255] % 12
        w = P[(s + 1 + P[(t + 1) & 255]) & 255] % 12
        return 70.0 * (self._c(u, h, k, 0.5) + self._c(v, n, o, 0.5) + self._c(w, q, r, 0.5))


class _PerlinSimplex:
    def __init__(self, seed, octaves):
        import mineshaft118 as ms
        r = ms.Lcg(seed)
        i, j = -octaves[0], octaves[-1]
        k = i + j + 1
        first = _Simplex(r)
        lv = [None] * k
        if 0 <= j < k and 0 in octaves:
            lv[j] = first
        for m in range(j + 1, k):
            if j - m in octaves:
                lv[m] = _Simplex(r)
            else:
                for _ in range(262):
                    r.next(32)
        assert j == 0
        self.lv = lv
        self.f_in = 2.0 ** j
        self.f_val = 1.0 / (2.0 ** k - 1.0)

    def value(self, x, y):
        d, e, f = 0.0, self.f_in, self.f_val
        for s in self.lv:
            if s is not None:
                d += s.value(x * e, y * e) * f
            e /= 2.0
            f *= 2.0
        return d


_FROZEN_T = None
_INFO = None


def _melt(x, z, base):
    """Biome.shouldMeltFrozenOceanIcebergSlightly at (x, 63, z) (TemperatureModifier.FROZEN)."""
    return _temp(x, z, base) > 0.1


def _temp(x, z, base):
    """The temperature of a FROZEN-modifier biome (frozen oceans) below y 80."""
    global _FROZEN_T, _INFO
    if _FROZEN_T is None:
        _FROZEN_T = _PerlinSimplex(3456, [-2, -1, 0])
        _INFO = _PerlinSimplex(2345, [0])
    t = base
    a = _FROZEN_T.value(x * 0.05, z * 0.05) * 7.0
    b = _INFO.value(x * 0.2, z * 0.2)
    if a + b < 0.3 and _INFO.value(x * 0.09, z * 0.09) < 0.8:
        t = 0.2
    return t


class Ice:
    """The frozen-ocean extension of world `seed`."""

    def __init__(self, seed, mc):
        import struct_info
        self.seed, self.mc = int(seed), int(mc)
        root = Xoro.from_seed(self.seed).fork_positional()
        self.root = root
        self.pillar = Normal(root.from_hash("minecraft:iceberg_pillar"), -6, [1.0, 1.0, 1.0, 1.0])
        self.roof = Normal(root.from_hash("minecraft:iceberg_pillar_roof"), -3, [1.0])
        self.surf = Normal(root.from_hash("minecraft:iceberg_surface"), -6, [1.0, 1.0, 1.0])
        self.lib = struct_info._lib(self.seed, self.mc, None, 0)

    def freezes(self, x, z):
        """freeze_top_layer turns the water surface (y 62) to ice: frozen ocean outside
        its warmer patches (Biome.shouldFreeze: temperature < 0.15)."""
        b = int(self.lib.get_biome_at(x, z, scale=1, y=SEA))
        if b == FROZEN_OCEAN:
            return _temp(x, z, 0.0) < 0.15
        return False

    def column(self, x, z, get):
        """{y: PICE / SNOWB} the extension puts in column (x, z); get(y) → terrain code."""
        b = int(self.lib.get_biome_at(x, z, scale=1, y=SEA))
        if b not in (FROZEN_OCEAN, DEEP_FROZEN_OCEAN):
            return {}
        ice = min(abs(self.surf.value(x, 0.0, z) * 8.25), self.pillar.value(x * 1.28, 0.0, z * 1.28) * 15.0)
        if ice <= 1.8:
            return {}
        roof = abs(self.roof.value(x * 1.17, 0.0, z * 1.17) * 1.5)
        top = min(ice * ice * 1.2, math.ceil(roof * 40.0) + 14.0)
        if _melt(x, z, 0.5 if b == DEEP_FROZEN_OCEAN else 0.0):
            top -= 2.0
        if top > 2.0:
            bottom = SEA - top - 7.0
            top += SEA
        else:
            top = bottom = 0.0
        r = self.root.at(x, 0, z)
        max_snow = 2 + r.next_int(4)
        min_snow_y = SEA + 18 + r.next_int(10)
        snow = 0
        height = SEA                                  # WORLD_SURFACE_WG + 1 over the sea
        y = 319
        while y >= SEA and get(y) == AIR:
            y -= 1
        height = y + 1
        out = {}
        it, ib = int(top), int(bottom)
        for y in range(max(height, it + 1), -64, -1):
            v = get(y)
            if v not in (AIR, WATER):
                if v == 0:
                    break                             # the floor: nothing below can qualify
                continue
            if v == AIR:
                ok = y < it and r.next_double() > 0.01
            else:
                ok = y > ib and y < SEA and bottom != 0.0 and r.next_double() > 0.15
            if not ok:
                continue
            if snow <= max_snow and y > min_snow_y:
                out[y] = SNOWB
                snow += 1
            else:
                out[y] = PICE
        return out
