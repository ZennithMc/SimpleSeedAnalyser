"""
fortress_loot.py — (30/10) the chests of the Nether fortresses, 1.16.1 → 26.3.

Pieces: NetherBridgePieces ported (same order and draws as libcubiomes
getFortressPieces), plus what cubiomes leaves out:
  - the small corridor turns (left / right) draw isNeedingChest = nextInt(3) == 0
    when they are created;
  - after the pieces, moveInsideHeights(random, 48, 70) moves the fortress down
    (usually to y 48: the fortress is taller than 23 blocks).
Chests: the turn's postProcess puts it at local (3, 2, 3) (left turn) or (1, 2, 3)
(right turn) with createChest → LootTableSeed = random.nextLong(). The random: the
chunk of the chest's (UNDERGROUND_DECORATION, fortress index); the fortress pieces
crossing that chunk run in order, and only chests draw.
"""
from __future__ import annotations

import stronghold_eyes as se

UNDERGROUND_DECORATION = 7

START, B_STRAIGHT, B_CROSS, B_FORT_CROSS, B_STAIRS, B_SPAWNER, B_ENTRANCE = range(7)
C_STRAIGHT, C_CROSS, C_RIGHT, C_LEFT, C_STAIRS, C_T, C_WART, END = range(7, 15)

# offset, size (w-1, h-1, d-1), extra draws, allowInRow, weight, max, name
_INFO = [
    ((0, 0, 0), (18, 9, 18), 0, 0, 0, 0, "start"),
    ((-1, -3, 0), (4, 9, 18), 0, 1, 30, 0, "bridge"),
    ((-8, -3, 0), (18, 9, 18), 0, 0, 10, 4, "bridge crossing"),
    ((-2, 0, 0), (6, 8, 6), 0, 0, 10, 4, "room crossing"),
    ((-2, 0, 0), (6, 10, 6), 0, 0, 10, 3, "stairs room"),
    ((-2, 0, 0), (6, 7, 8), 0, 0, 5, 2, "blaze spawner"),
    ((-5, -3, 0), (12, 13, 12), 0, 0, 5, 1, "castle entrance"),
    ((-1, 0, 0), (4, 6, 4), 0, 1, 25, 0, "corridor"),
    ((-1, 0, 0), (4, 6, 4), 0, 0, 15, 5, "corridor crossing"),
    ((-1, 0, 0), (4, 6, 4), 1, 0, 5, 10, "corridor right turn"),
    ((-1, 0, 0), (4, 6, 4), 1, 0, 5, 10, "corridor left turn"),
    ((-1, -7, 0), (4, 13, 9), 0, 1, 10, 3, "corridor stairs"),
    ((-3, 0, 0), (8, 6, 8), 0, 0, 7, 2, "corridor T balcony"),
    ((-5, -3, 0), (12, 13, 12), 0, 0, 5, 2, "nether wart room"),
    ((-1, -3, 0), (4, 9, 7), 1, 0, 0, 0, "bridge end"),
]


class Piece:
    __slots__ = ("typ", "b0", "b1", "rot", "depth", "chest")

    def __init__(self, typ, b0, b1, rot, depth):
        self.typ, self.b0, self.b1, self.rot, self.depth = typ, b0, b1, rot, depth
        self.chest = False

    def world(self, x, y, z):
        """StructurePiece.getWorldX / Y / Z (orientation: 0 N, 1 E, 2 S, 3 W)."""
        (x0, y0, z0), (x1, _y1, z1) = self.b0, self.b1
        r = self.rot
        if r == 0:
            return x0 + x, y0 + y, z1 - z
        if r == 2:
            return x0 + x, y0 + y, z0 + z
        if r == 3:
            return x1 - z, y0 + y, z0 + x
        return x0 + z, y0 + y, z0 + x


class _Gen:
    def __init__(self, rng):
        self.r = rng
        self.list = []
        self.ntyp = [0] * 15
        self.ntyp[START] = 1
        self.last = START
        self.pending = []

    def add(self, typ, x, y, z, depth, facing, pending):
        (dx, dy, dz), (sx, sy, sz) = _INFO[typ][0], _INFO[typ][1]
        y0, y1 = y + dy, y + dy + sy
        if facing == 0:
            b0, b1 = (x + dx, y0, z + dz - sz), (x + dx + sx, y1, z + dz)
        elif facing == 1:
            b0, b1 = (x + dz, y0, z + dx), (x + dz + sz, y1, z + dx + sx)
        elif facing == 2:
            b0, b1 = (x + dx, y0, z + dz), (x + dx + sx, y1, z + dz + sz)
        else:
            b0, b1 = (x + dz - sz, y0, z + dx), (x + dz, y1, z + dx + sx)
        for q in self.list:
            if (q.b1[0] >= b0[0] and q.b0[0] <= b1[0] and q.b1[2] >= b0[2] and q.b0[2] <= b1[2]
                    and q.b1[1] >= b0[1] and q.b0[1] <= b1[1]):
                return None
        p = Piece(typ, b0, b1, facing, depth)
        if typ in (C_RIGHT, C_LEFT):
            p.chest = self.r.nextInt_bound(3) == 0
        elif typ == END:
            self.r.next(32)                                   # selfSeed = nextInt()
        if pending:
            self.list.append(p)
            self.ntyp[typ] += 1
            if typ != END:
                self.last = typ
            self.pending.append(p)
        return p

    def extend(self, p, offh, offv, turn, corridor):
        depth = p.depth + 1
        facing = p.rot
        t0 = C_STRAIGHT if corridor else B_STRAIGHT
        t1 = t0 + (7 if corridor else 6)
        y = p.b0[1] + offv
        if turn == 0:
            x, z = ((p.b0[0] + offh, p.b0[2] - 1), (p.b1[0] + 1, p.b0[2] + offh),
                    (p.b0[0] + offh, p.b1[2] + 1), (p.b0[0] - 1, p.b0[2] + offh))[facing]
        elif turn == -1:
            if facing & 1:
                x, z, facing = p.b0[0] + offh, p.b0[2] - 1, 0
            else:
                x, z, facing = p.b0[0] - 1, p.b0[2] + offh, 3
        else:
            if facing & 1:
                x, z, facing = p.b0[0] + offh, p.b1[2] + 1, 2
            else:
                x, z, facing = p.b1[0] + 1, p.b0[2] + offh, 1
        valid = -1
        st = self.list[0]
        if abs(x - st.b0[0]) <= 112 and abs(z - st.b0[2]) <= 112:
            valid, tot = 0, 0
            for t in range(t0, t1):
                mx = _INFO[t][5]
                if mx > 0 and self.ntyp[t] >= mx:
                    continue
                if mx > 0:
                    valid = 1
                tot += _INFO[t][4]
            if valid and tot > 0 and depth <= 30:
                for _ in range(5):
                    n = self.r.nextInt_bound(tot)
                    for t in range(t0, t1):
                        mx = _INFO[t][5]
                        if mx > 0 and self.ntyp[t] >= mx:
                            continue
                        n -= _INFO[t][4]
                        if n >= 0:
                            continue
                        if self.last == t and not _INFO[t][3]:
                            break
                        if self.add(t, x, y, z, depth, facing, True) is not None:
                            return
        self.add(END, x, y, z, depth, facing, valid >= 0)

    def children(self, p):
        t, e = p.typ, self.extend
        if t == B_STRAIGHT:
            e(p, 1, 3, 0, 0)
        elif t in (B_CROSS, START):
            e(p, 8, 3, 0, 0); e(p, 8, 3, -1, 0); e(p, 8, 3, 1, 0)
        elif t == B_FORT_CROSS:
            e(p, 2, 0, 0, 0); e(p, 2, 0, -1, 0); e(p, 2, 0, 1, 0)
        elif t == B_STAIRS:
            e(p, 2, 6, 1, 0)
        elif t == B_ENTRANCE:
            e(p, 5, 3, 0, 1)
        elif t in (C_STRAIGHT, C_STAIRS):
            e(p, 1, 0, 0, 1)
        elif t == C_CROSS:
            e(p, 1, 0, 0, 1); e(p, 1, 0, -1, 1); e(p, 1, 0, 1, 1)
        elif t == C_RIGHT:
            e(p, 1, 0, 1, 1)
        elif t == C_LEFT:
            e(p, 1, 0, -1, 1)
        elif t == C_T:
            h = 5 if p.rot in (0, 3) else 1
            e(p, h, 0, -1, self.r.nextInt_bound(8) != 0)
            e(p, h, 0, 1, self.r.nextInt_bound(8) != 0)
        elif t == C_WART:
            e(p, 5, 3, 0, 1); e(p, 5, 11, 0, 1)


_CACHE = {}


def pieces(seed, mc, cx, cz):
    """[Piece] of the fortress started in chunk (cx, cz), moved to its final Y."""
    k = (int(seed), int(mc), int(cx), int(cz))
    if k in _CACHE:
        return _CACHE[k]
    r = se.JavaRandom(0)
    r.setLargeFeatureSeed(int(seed), int(cx), int(cz))
    g = _Gen(r)
    x, z = cx * 16 + 2, cz * 16 + 2
    st = Piece(START, (x, 64, z), (x + 18, 73, z + 18), 0, 0)
    st.rot = r.nextInt_bound(4)
    g.list.append(st)
    g.children(st)
    while g.pending:
        i = r.nextInt_bound(len(g.pending))
        g.children(g.pending.pop(i))
    lo = min(p.b0[1] for p in g.list)
    hi = max(p.b1[1] for p in g.list)
    n = 70 - 48 + 1 - (hi - lo + 1)
    dy = (48 + r.nextInt_bound(n) if n > 1 else 48) - lo
    for p in g.list:
        p.b0 = (p.b0[0], p.b0[1] + dy, p.b0[2])
        p.b1 = (p.b1[0], p.b1[1] + dy, p.b1[2])
    if len(_CACHE) > 256:
        _CACHE.clear()
    _CACHE[k] = g.list
    return g.list


def fortress_index(mc):
    """The fortress's index in UNDERGROUND_DECORATION: alone up to 1.18.2, before
    nether_fossil in 1.19.2 (registration order), after ancient_city from 1.19.4
    (sorted by name)."""
    return 1 if mc >= 24 else 0


def chest_pos(p):
    return p.world(3, 2, 3) if p.typ == C_LEFT else p.world(1, 2, 3)


def chest_list(seed, mc, x, z):
    """[(x, y, z, loot seed, piece)] of the fortress whose start is at block (x, z)."""
    import structure_loot as sl
    cx, cz = int(x) >> 4, int(z) >> 4
    ps = pieces(seed, mc, cx, cz)
    out = []
    for i, p in enumerate(ps):
        if not p.chest:
            continue
        X, Y, Z = chest_pos(p)
        ch = (X >> 4, Z >> 4)
        r = sl.structure_rng(seed, mc, ch[0], ch[1], fortress_index(mc), UNDERGROUND_DECORATION)
        s = None
        for q in ps:
            if not q.chest or not (q.b1[0] >= ch[0] << 4 and q.b0[0] <= (ch[0] << 4) + 15
                                   and q.b1[2] >= ch[1] << 4 and q.b0[2] <= (ch[1] << 4) + 15):
                continue
            w = chest_pos(q)
            if (w[0] >> 4, w[2] >> 4) != ch:
                continue
            v = sl._next_long(r)
            if q is p:
                s = v
                break
        out.append((X, Y, Z, s, p))
    return out


def chests(seed, mc, x, z, ver):
    import loot_engine as le
    lst = chest_list(seed, mc, x, z)
    if not lst:
        return [dict(name="No chest", pos=(int(x), None, int(z)), table="", seed=None, slots=None,
                     approx=[], note="No chest in this fortress: none of its corridor turns got one "
                                     "(each has a 1 in 3 chance).")]
    out = []
    for i, (X, Y, Z, s, p) in enumerate(lst):
        slots, approx = le.fill_chest("nether_bridge", s, ver)
        out.append(dict(name=f"Chest {i + 1}", pos=(X, Y, Z), table="chests/nether_bridge", seed=s,
                        slots=slots, approx=approx,
                        note=f"In a small {_INFO[p.typ][6]} of the castle (the indoor corridors)."))
    return out


def map_boxes(seed, mc, x, z):
    return [(p.b0[0], p.b0[2], p.b1[0], p.b1[2], p.typ, p.rot, p.b0[1], p.b1[1])
            for p in pieces(seed, mc, int(x) >> 4, int(z) >> 4)]
