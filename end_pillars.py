"""
end_pillars.py — (06/11) the 10 obsidian pillars of the End → 16 bits of the structure
seed (any version 1.9+: SpikeFeature).

The game draws a "pillar seed" = new Random(worldSeed).nextLong() & 0xFFFF — it only
depends on the 48 low bits of the world seed (the structure seed), in fact on its 32
low bits — then shuffles the 10 sizes 0..9 with new Random(pillarSeed). Pillar i
stands at angle 2·(−π + i·π/10) on a circle of radius 42 (i = 0 east, then towards
the south: clockwise seen from above with north up); size l gives the height 76 + 3 l
(bedrock + crystal on top: the last obsidian block is at y = 75 + 3 l), the radius
2 + l / 3 and an iron-bar cage when l is 1 or 2. Checked against 1.16.1 / 1.16.5 /
1.17.1 servers (seed 12345: 10/10 heights and cages).

    obs = [None] * 10; obs[0] = 9 ...          # sizes seen (0 shortest .. 9 tallest)
    seeds = matching(obs)                       # pillar seeds that fit
    keep = [s for s in structure_seeds if pillar_seed(s) in seeds]
"""
from __future__ import annotations

import math

from loot_engine import JavaRandom

CAGED = (1, 2)
_TABLE = None


def pillar_seed(structure_seed: int) -> int:
    return JavaRandom(int(structure_seed) & ((1 << 48) - 1)).next_long() & 0xFFFF


def sizes(ps: int) -> tuple:
    """size (0..9) of pillar 0..9 for pillar seed ps (Collections.shuffle)"""
    r = JavaRandom(ps)
    lst = list(range(10))
    for i in range(10, 1, -1):
        j = r.next_int(i)
        lst[i - 1], lst[j] = lst[j], lst[i - 1]
    return tuple(lst)


def table() -> list:
    global _TABLE
    if _TABLE is None:
        _TABLE = [sizes(ps) for ps in range(1 << 16)]
    return _TABLE


def position(i: int) -> tuple:
    a = 2.0 * (-math.pi + (math.pi / 10.0) * i)
    return math.floor(42.0 * math.cos(a)), math.floor(42.0 * math.sin(a))


def height(size: int) -> int:
    return 76 + 3 * size


def top_obsidian(size: int) -> int:
    return 75 + 3 * size


def radius(size: int) -> int:
    return 2 + size // 3


def _fits(lay, obs, cages) -> bool:
    for i in range(10):
        o = obs[i]
        if o is not None and lay[i] != o:
            return False
        c = cages[i] if cages else None
        if c is not None and (lay[i] in CAGED) != bool(c):
            return False
    return True


def _np_table():
    global _NP
    if _NP is None:
        import numpy as np
        _NP = np.array(table(), dtype=np.int8)
    return _NP


_NP = None


def _variants(obs, cages, rotation_unknown, mirror_unknown):
    out = []
    for m in ((False, True) if mirror_unknown else (False,)):
        for rot in (range(10) if rotation_unknown else (0,)):
            o2, c2 = [None] * 10, [None] * 10
            for i in range(10):
                j = ((-i if m else i) + rot) % 10
                o2[j], c2[j] = obs[i], cages[i]
            if (o2, c2) not in out:
                out.append((o2, c2))
    return out


def matching(obs, cages=None, rotation_unknown=False, mirror_unknown=False) -> list:
    """pillar seeds whose pillars fit: obs[i] = size of pillar i (None = unknown),
    cages[i] = True / False / None. rotation_unknown: the pillars may be turned
    (which one is east is not known); mirror_unknown: seen the other way round."""
    obs = (list(obs) + [None] * 10)[:10]
    cages = (list(cages) + [None] * 10)[:10] if cages else [None] * 10
    if all(o is None for o in obs) and all(c is None for c in cages):
        return list(range(1 << 16))
    variants = _variants(obs, cages, rotation_unknown, mirror_unknown)
    try:
        import numpy as np
        t = _np_table()
        caged = (t == 1) | (t == 2)
        hit = np.zeros(len(t), dtype=bool)
        for o2, c2 in variants:
            ok = np.ones(len(t), dtype=bool)
            for i in range(10):
                if o2[i] is not None:
                    ok &= t[:, i] == o2[i]
                if c2[i] is not None:
                    ok &= caged[:, i] == bool(c2[i])
            hit |= ok
        return np.nonzero(hit)[0].tolist()
    except ImportError:
        return [ps for ps, lay in enumerate(table())
                if any(_fits(lay, o2, c2) for o2, c2 in variants)]


def bits(n_matching: int) -> float:
    return math.log2((1 << 16) / max(1, n_matching))


def filter_seeds(structure_seeds, pillar_seeds) -> list:
    ok = set(pillar_seeds)
    return [s for s in structure_seeds if pillar_seed(s) in ok]
