"""
mansion_loot.py — (30/10) woodland mansions: their pieces (WoodlandMansionPieces,
MansionGrid + MansionPiecePlacer, ported from the 26.2 jar) and the loot of their
chests, 1.16.1 → 26.3.

Start: the structure random (setLargeFeatureSeed): Rotation.getRandom, then the grid
and the rooms with the same random. Position: block 7 of the start chunk, Y = the
lowest WORLD_SURFACE_WG occupied height of the 4 corners of a 5 × 5 box (rotated).
Chests: the « Chest… » data markers (createChest: woodland_mansion table,
random.nextLong() when the spot is in the chunk). The random: the chunk's
(SURFACE_STRUCTURES, mansion index); every piece crossing the chunk in order: the
template's chests (empty, but a LootTableSeed draw each in placeInWorld) in the
chunk, then its Chest markers in the chunk.
"""
from __future__ import annotations

import gzip
import json
import os

import mineshaft118 as ms

N, E, S, W = (0, -1), (1, 0), (0, 1), (-1, 0)
HORIZONTAL = (N, E, S, W)
_CW = {N: E, E: S, S: W, W: N}
_CCW = {v: k for k, v in _CW.items()}
_OPP = {N: S, S: N, E: W, W: E}
UP = (0, 0, 1)                                      # a marker, not a horizontal step
FROM2D = (S, W, N, E)
NONE, CW90, CW180, CCW90 = 0, 1, 2, 3
M_NONE, M_LR, M_FB = 0, 1, 2

ROOM_1x1, ROOM_1x2, ROOM_2x2 = 65536, 131072, 262144
ORIGIN, DOOR, STAIRS, CORRIDOR_F = 0x100000, 0x200000, 0x400000, 0x800000


def rot_dir(rot, d):
    for _ in range(rot & 3):
        d = _CW[d]
    return d


def rot_pos(rot, x, z):
    """BlockPos.rotate (offsets)."""
    rot &= 3
    if rot == CW90:
        return -z, x
    if rot == CW180:
        return -x, -z
    if rot == CCW90:
        return z, -x
    return x, z


def rel(p, d, n):
    return (p[0] + d[0] * n, p[1], p[2] + d[1] * n)


def above(p, n):
    return (p[0], p[1] + n, p[2])


class Grid:
    def __init__(self, w, h, outside):
        self.width, self.height, self.out = w, h, outside
        self.g = [[0] * h for _ in range(w)]

    def set(self, x, y, v):
        if 0 <= x < self.width and 0 <= y < self.height:
            self.g[x][y] = v

    def setr(self, x0, y0, x1, y1, v):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.set(x, y, v)

    def get(self, x, y):
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.g[x][y]
        return self.out

    def setif(self, x, y, ifv, v):
        if self.get(x, y) == ifv:
            self.set(x, y, v)

    def edges_to(self, x, y, v):
        return self.get(x - 1, y) == v or self.get(x + 1, y) == v or self.get(x, y + 1) == v or self.get(x, y - 1) == v


def is_house(g, x, y):
    return g.get(x, y) in (1, 2, 3, 4)


class MansionGrid:
    def __init__(self, r):
        self.r = r
        self.ex, self.ey = 7, 4
        ex, ey = self.ex, self.ey
        b = self.base = Grid(11, 11, 5)
        b.setr(ex, ey, ex + 1, ey + 1, 3)
        b.setr(ex - 1, ey, ex - 1, ey + 1, 2)
        b.setr(ex + 2, ey - 2, ex + 3, ey + 3, 5)
        b.setr(ex + 1, ey - 2, ex + 1, ey - 1, 1)
        b.setr(ex + 1, ey + 2, ex + 1, ey + 3, 1)
        b.set(ex - 1, ey - 1, 1)
        b.set(ex - 1, ey + 2, 1)
        b.setr(0, 0, 11, 1, 5)
        b.setr(0, 9, 11, 11, 5)
        self.corridor(b, ex, ey - 2, W, 6)
        self.corridor(b, ex, ey + 3, W, 6)
        self.corridor(b, ex - 2, ey - 1, W, 3)
        self.corridor(b, ex - 2, ey + 2, W, 3)
        while self.clean(b):
            pass
        self.rooms = [Grid(11, 11, 5), Grid(11, 11, 5), Grid(11, 11, 5)]
        self.identify(b, self.rooms[0])
        self.identify(b, self.rooms[1])
        self.rooms[0].setr(ex + 1, ey, ex + 1, ey + 1, CORRIDOR_F)
        self.rooms[1].setr(ex + 1, ey, ex + 1, ey + 1, CORRIDOR_F)
        self.third = Grid(11, 11, 5)
        self.setup_third()
        self.identify(self.third, self.rooms[2])

    def is_room_id(self, g, x, y, floor, rid):
        return (self.rooms[floor].get(x, y) & 0xFFFF) == rid

    def dir_1x2(self, g, x, y, floor, rid):
        for d in HORIZONTAL:
            if self.is_room_id(g, x + d[0], y + d[1], floor, rid):
                return d
        return None

    def corridor(self, g, x, y, h, depth):
        if depth <= 0:
            return
        r = self.r
        g.set(x, y, 1)
        g.setif(x + h[0], y + h[1], 0, 1)
        for _a in range(8):
            nd = FROM2D[r.next_int(4)]
            if nd == _OPP[h] or (nd == E and r.next(1) != 0):
                continue
            nx, ny = x + h[0], y + h[1]
            if g.get(nx + nd[0], ny + nd[1]) != 0 or g.get(nx + nd[0] * 2, ny + nd[1] * 2) != 0:
                continue
            self.corridor(g, x + h[0] + nd[0], y + h[1] + nd[1], nd, depth - 1)
            break
        cw, ccw = _CW[h], _CCW[h]
        g.setif(x + cw[0], y + cw[1], 0, 2)
        g.setif(x + ccw[0], y + ccw[1], 0, 2)
        g.setif(x + h[0] + cw[0], y + h[1] + cw[1], 0, 2)
        g.setif(x + h[0] + ccw[0], y + h[1] + ccw[1], 0, 2)
        g.setif(x + h[0] * 2, y + h[1] * 2, 0, 2)
        g.setif(x + cw[0] * 2, y + cw[1] * 2, 0, 2)
        g.setif(x + ccw[0] * 2, y + ccw[1] * 2, 0, 2)

    @staticmethod
    def clean(g):
        touched = False
        for y in range(g.height):
            for x in range(g.width):
                if g.get(x, y) != 0:
                    continue
                n = is_house(g, x + 1, y) + is_house(g, x - 1, y) + is_house(g, x, y + 1) + is_house(g, x, y - 1)
                if n >= 3:
                    g.set(x, y, 2)
                    touched = True
                    continue
                if n != 2:
                    continue
                dn = (is_house(g, x + 1, y + 1) + is_house(g, x - 1, y + 1) + is_house(g, x + 1, y - 1)
                      + is_house(g, x - 1, y - 1))
                if dn > 1:
                    continue
                g.set(x, y, 2)
                touched = True
        return touched

    def setup_third(self):
        r = self.r
        pot = []
        floor = self.rooms[1]
        t = self.third
        for y in range(t.height):
            for x in range(t.width):
                d = floor.get(x, y)
                if (d & 0xF0000) != ROOM_1x2 or (d & DOOR) != DOOR:
                    continue
                pot.append((x, y))
        if not pot:
            t.setr(0, 0, t.width, t.height, 5)
            return
        rx, ry = pot[r.next_int(len(pot))]
        d = floor.get(rx, ry)
        floor.set(rx, ry, d | STAIRS)
        rd = self.dir_1x2(self.base, rx, ry, 1, d & 0xFFFF)
        ex, ey = rx + rd[0], ry + rd[1]
        for y in range(t.height):
            for x in range(t.width):
                if not is_house(self.base, x, y):
                    t.set(x, y, 5)
                    continue
                if x == rx and y == ry:
                    t.set(x, y, 3)
                    continue
                if x != ex or y != ey:
                    continue
                t.set(x, y, 3)
                self.rooms[2].set(x, y, CORRIDOR_F)
        pc = [dd for dd in HORIZONTAL if t.get(ex + dd[0], ey + dd[1]) == 0]
        if not pc:
            t.setr(0, 0, t.width, t.height, 5)
            floor.set(rx, ry, d)
            return
        cd = pc[r.next_int(len(pc))]
        self.corridor(t, ex + cd[0], ey + cd[1], cd, 4)
        while self.clean(t):
            pass

    def identify(self, frm, rg):
        r = self.r
        pos = [(x, y) for y in range(frm.height) for x in range(frm.width) if frm.get(x, y) == 2]
        for i in range(len(pos), 1, -1):                 # Util.shuffle
            j = r.next_int(i)
            pos[i - 1], pos[j] = pos[j], pos[i - 1]
        rid = 10
        for (x, y) in pos:
            if rg.get(x, y) != 0:
                continue
            x0 = x1 = x
            y0 = y1 = y
            typ = ROOM_1x1
            if rg.get(x + 1, y) == 0 and rg.get(x, y + 1) == 0 and rg.get(x + 1, y + 1) == 0 and \
                    frm.get(x + 1, y) == 2 and frm.get(x, y + 1) == 2 and frm.get(x + 1, y + 1) == 2:
                x1 += 1; y1 += 1; typ = ROOM_2x2
            elif rg.get(x - 1, y) == 0 and rg.get(x, y + 1) == 0 and rg.get(x - 1, y + 1) == 0 and \
                    frm.get(x - 1, y) == 2 and frm.get(x, y + 1) == 2 and frm.get(x - 1, y + 1) == 2:
                x0 -= 1; y1 += 1; typ = ROOM_2x2
            elif rg.get(x - 1, y) == 0 and rg.get(x, y - 1) == 0 and rg.get(x - 1, y - 1) == 0 and \
                    frm.get(x - 1, y) == 2 and frm.get(x, y - 1) == 2 and frm.get(x - 1, y - 1) == 2:
                x0 -= 1; y0 -= 1; typ = ROOM_2x2
            elif rg.get(x + 1, y) == 0 and frm.get(x + 1, y) == 2:
                x1 += 1; typ = ROOM_1x2
            elif rg.get(x, y + 1) == 0 and frm.get(x, y + 1) == 2:
                y1 += 1; typ = ROOM_1x2
            elif rg.get(x - 1, y) == 0 and frm.get(x - 1, y) == 2:
                x0 -= 1; typ = ROOM_1x2
            elif rg.get(x, y - 1) == 0 and frm.get(x, y - 1) == 2:
                y0 -= 1; typ = ROOM_1x2
            dx = x0 if r.next(1) != 0 else x1
            dy = y0 if r.next(1) != 0 else y1
            flag = DOOR
            if not frm.edges_to(dx, dy, 1):
                dx = x1 if dx == x0 else x0
                dy = y1 if dy == y0 else y0
                if not frm.edges_to(dx, dy, 1):
                    dy = y1 if dy == y0 else y0
                    if not frm.edges_to(dx, dy, 1):
                        dx = x1 if dx == x0 else x0
                        dy = y1 if dy == y0 else y0
                        if not frm.edges_to(dx, dy, 1):
                            flag = 0
                            dx, dy = x0, y0
            for ry in range(y0, y1 + 1):
                for rx in range(x0, x1 + 1):
                    if rx == dx and ry == dy:
                        rg.set(rx, ry, ORIGIN | flag | typ | rid)
                    else:
                        rg.set(rx, ry, typ | rid)
            rid += 1


class _Floor1:
    def r1x1(self, r): return f"1x1_a{r.next_int(5) + 1}"
    def r1x1s(self, r): return f"1x1_as{r.next_int(4) + 1}"
    def r1x2side(self, r, st): return f"1x2_a{r.next_int(9) + 1}"
    def r1x2front(self, r, st): return f"1x2_b{r.next_int(5) + 1}"
    def r1x2s(self, r): return f"1x2_s{r.next_int(2) + 1}"
    def r2x2(self, r): return f"2x2_a{r.next_int(4) + 1}"
    def r2x2s(self, r): return "2x2_s1"


class _Floor2(_Floor1):
    old = False                 # 1.16 → 1.18.2: 4 « 1x1_b » rooms (1x1_b5, the allays' cell: 1.19)

    def r1x1(self, r): return f"1x1_b{r.next_int(4 if self.old else 5) + 1}"
    def r1x2side(self, r, st): return "1x2_c_stairs" if st else f"1x2_c{r.next_int(4) + 1}"
    def r1x2front(self, r, st): return "1x2_d_stairs" if st else f"1x2_d{r.next_int(5) + 1}"
    def r1x2s(self, r): return f"1x2_se{r.next_int(1) + 1}"
    def r2x2(self, r): return f"2x2_b{r.next_int(5) + 1}"


def _zero_pos(x, z, mirror, rot, sx, sz):
    """StructureTemplate.getZeroPositionWithTransform."""
    sx -= 1; sz -= 1
    i = sx if mirror == M_FB else 0
    j = sz if mirror == M_LR else 0
    rot &= 3
    if rot == NONE:
        return x + i, z + j
    if rot == CW90:
        return x + sz - j, z + i
    if rot == CW180:
        return x + sx - i, z + sz - j
    return x + j, z + sx - i


class Placer:
    def __init__(self, r, old=False):
        self.r = r
        self.old = old
        self.pieces = []

    def add(self, name, pos, rot, mirror=M_NONE):
        self.pieces.append((name, pos, rot & 3, mirror))

    def create(self, origin, rot, m):
        r = self.r
        data = dict(pos=origin, rot=rot, wall="wall_flat")
        second = dict(pos=None, rot=None, wall="wall_window")
        self.entrance(data)
        second["pos"] = above(data["pos"], 8)
        second["rot"] = data["rot"]
        base, third = m.base, m.third
        self.sx, self.sy = m.ex + 1, m.ey + 1
        endx, endy = m.ex + 1, m.ey
        self.outer(data, base, S, self.sx, self.sy, endx, endy)
        self.outer(second, base, S, self.sx, self.sy, endx, endy)
        td = dict(pos=above(data["pos"], 19), rot=data["rot"], wall="wall_window")
        done = False
        for y in range(third.height):
            if done:
                break
            for x in range(third.width - 1, -1, -1):
                if done:
                    break
                if not is_house(third, x, y):
                    continue
                td["pos"] = rel(td["pos"], rot_dir(rot, S), 8 + (y - self.sy) * 8)
                td["pos"] = rel(td["pos"], rot_dir(rot, E), (x - self.sx) * 8)
                self.wall_piece(td)
                self.outer(td, third, S, x, y, x, y)
                done = True
        self.roof(above(origin, 16), rot, base, third)
        self.roof(above(origin, 27), rot, third, None)
        cols = (_Floor1(), _Floor2(), _Floor2())
        cols[1].old = cols[2].old = self.old
        for fl in range(3):
            fo = above(origin, 8 * fl + (3 if fl == 2 else 0))
            rooms = m.rooms[fl]
            grid = third if fl == 2 else base
            sp = "carpet_south_1" if fl == 0 else "carpet_south_2"
            wp = "carpet_west_1" if fl == 0 else "carpet_west_2"
            for y in range(grid.height):
                for x in range(grid.width):
                    if grid.get(x, y) != 1:
                        continue
                    pos = rel(fo, rot_dir(rot, S), 8 + (y - self.sy) * 8)
                    pos = rel(pos, rot_dir(rot, E), (x - self.sx) * 8)
                    self.add("corridor_floor", pos, rot)
                    if grid.get(x, y - 1) == 1 or (rooms.get(x, y - 1) & CORRIDOR_F) == CORRIDOR_F:
                        self.add("carpet_north", above(rel(pos, rot_dir(rot, E), 1), 1), rot)
                    if grid.get(x + 1, y) == 1 or (rooms.get(x + 1, y) & CORRIDOR_F) == CORRIDOR_F:
                        self.add("carpet_east", above(rel(rel(pos, rot_dir(rot, S), 1), rot_dir(rot, E), 5), 1), rot)
                    if grid.get(x, y + 1) == 1 or (rooms.get(x, y + 1) & CORRIDOR_F) == CORRIDOR_F:
                        self.add(sp, rel(rel(pos, rot_dir(rot, S), 5), rot_dir(rot, W), 1), rot)
                    if grid.get(x - 1, y) != 1 and (rooms.get(x - 1, y) & CORRIDOR_F) != CORRIDOR_F:
                        continue
                    self.add(wp, rel(rel(pos, rot_dir(rot, W), 1), rot_dir(rot, N), 1), rot)
            wall = "indoors_wall_1" if fl == 0 else "indoors_wall_2"
            door = "indoors_door_1" if fl == 0 else "indoors_door_2"
            for y in range(grid.height):
                for x in range(grid.width):
                    tfs = fl == 2 and grid.get(x, y) == 3
                    if grid.get(x, y) != 2 and not tfs:
                        continue
                    rd = rooms.get(x, y)
                    rtype = rd & 0xF0000
                    rid = rd & 0xFFFF
                    tfs = tfs and (rd & CORRIDOR_F) == CORRIDOR_F
                    dirs = []
                    if (rd & DOOR) == DOOR:
                        dirs = [d for d in HORIZONTAL if grid.get(x + d[0], y + d[1]) == 1]
                    dd = None
                    if dirs:
                        dd = dirs[r.next_int(len(dirs))]
                    elif (rd & ORIGIN) == ORIGIN:
                        dd = UP
                    rp = rel(fo, rot_dir(rot, S), 8 + (y - self.sy) * 8)
                    rp = rel(rp, rot_dir(rot, E), -1 + (x - self.sx) * 8)
                    if is_house(grid, x - 1, y) and not m.is_room_id(grid, x - 1, y, fl, rid):
                        self.add(door if dd == W else wall, rp, rot)
                    if grid.get(x + 1, y) == 1 and not tfs:
                        self.add(door if dd == E else wall, rel(rp, rot_dir(rot, E), 8), rot)
                    if is_house(grid, x, y + 1) and not m.is_room_id(grid, x, y + 1, fl, rid):
                        p = rel(rel(rp, rot_dir(rot, S), 7), rot_dir(rot, E), 7)
                        self.add(door if dd == S else wall, p, rot + CW90)
                    if grid.get(x, y - 1) == 1 and not tfs:
                        p = rel(rel(rp, rot_dir(rot, N), 1), rot_dir(rot, E), 7)
                        self.add(door if dd == N else wall, p, rot + CW90)
                    if rtype == ROOM_1x1:
                        self.room1x1(rp, rot, dd, cols[fl])
                        continue
                    if rtype == ROOM_1x2 and dd is not None:
                        rdir = m.dir_1x2(grid, x, y, fl, rid)
                        self.room1x2(rp, rot, rdir, dd, cols[fl], (rd & STAIRS) == STAIRS)
                        continue
                    if rtype == ROOM_2x2 and dd is not None and dd != UP:
                        rdir = _CW[dd]
                        if not m.is_room_id(grid, x + rdir[0], y + rdir[1], fl, rid):
                            rdir = _OPP[rdir]
                        self.room2x2(rp, rot, rdir, dd, cols[fl])
                        continue
                    if rtype != ROOM_2x2 or dd != UP:
                        continue
                    self.add(cols[fl].r2x2s(r), rel(rp, rot_dir(rot, E), 1), rot, M_NONE)

    def outer(self, data, g, gd, sx, sy, ex, ey):
        gx, gy = sx, sy
        start = gd
        while True:
            if not is_house(g, gx + gd[0], gy + gd[1]):
                self.turn(data)
                gd = _CW[gd]
                if not (gx == ex and gy == ey and start == gd):
                    self.wall_piece(data)
            elif is_house(g, gx + gd[0], gy + gd[1]) and \
                    is_house(g, gx + gd[0] + _CCW[gd][0], gy + gd[1] + _CCW[gd][1]):
                self.inner_turn(data)
                gx += gd[0]
                gy += gd[1]
                gd = _CCW[gd]
            else:
                gx += gd[0]
                gy += gd[1]
                if not (gx == ex and gy == ey and start == gd):
                    self.wall_piece(data)
            if gx == ex and gy == ey and start == gd:
                break

    def roof(self, ro, rot, g, ab):
        for y in range(g.height):
            for x in range(g.width):
                p = rel(rel(ro, rot_dir(rot, S), 8 + (y - self.sy) * 8), rot_dir(rot, E), (x - self.sx) * 8)
                isab = ab is not None and is_house(ab, x, y)
                if not is_house(g, x, y) or isab:
                    continue
                self.add("roof", above(p, 3), rot)
                if not is_house(g, x + 1, y):
                    self.add("roof_front", rel(p, rot_dir(rot, E), 6), rot)
                if not is_house(g, x - 1, y):
                    self.add("roof_front", rel(rel(p, rot_dir(rot, E), 0), rot_dir(rot, S), 7), rot + CW180)
                if not is_house(g, x, y - 1):
                    self.add("roof_front", rel(p, rot_dir(rot, W), 1), rot + CCW90)
                if is_house(g, x, y + 1):
                    continue
                self.add("roof_front", rel(rel(p, rot_dir(rot, E), 6), rot_dir(rot, S), 6), rot + CW90)
        if ab is not None:
            for y in range(g.height):
                for x in range(g.width):
                    p = rel(rel(ro, rot_dir(rot, S), 8 + (y - self.sy) * 8), rot_dir(rot, E), (x - self.sx) * 8)
                    if not is_house(g, x, y) or not is_house(ab, x, y):
                        continue
                    if not is_house(g, x + 1, y):
                        self.add("small_wall", rel(p, rot_dir(rot, E), 7), rot)
                    if not is_house(g, x - 1, y):
                        self.add("small_wall", rel(rel(p, rot_dir(rot, W), 1), rot_dir(rot, S), 6), rot + CW180)
                    if not is_house(g, x, y - 1):
                        self.add("small_wall", rel(rel(p, rot_dir(rot, W), 0), rot_dir(rot, N), 1), rot + CCW90)
                    if not is_house(g, x, y + 1):
                        self.add("small_wall", rel(rel(p, rot_dir(rot, E), 6), rot_dir(rot, S), 7), rot + CW90)
                    if not is_house(g, x + 1, y):
                        if not is_house(g, x, y - 1):
                            self.add("small_wall_corner", rel(rel(p, rot_dir(rot, E), 7), rot_dir(rot, N), 2), rot)
                        if not is_house(g, x, y + 1):
                            self.add("small_wall_corner", rel(rel(p, rot_dir(rot, E), 8), rot_dir(rot, S), 7),
                                     rot + CW90)
                    if is_house(g, x - 1, y):
                        continue
                    if not is_house(g, x, y - 1):
                        self.add("small_wall_corner", rel(rel(p, rot_dir(rot, W), 2), rot_dir(rot, N), 1), rot + CCW90)
                    if is_house(g, x, y + 1):
                        continue
                    self.add("small_wall_corner", rel(rel(p, rot_dir(rot, W), 1), rot_dir(rot, S), 8), rot + CW180)
        for y in range(g.height):
            for x in range(g.width):
                p = rel(rel(ro, rot_dir(rot, S), 8 + (y - self.sy) * 8), rot_dir(rot, E), (x - self.sx) * 8)
                isab = ab is not None and is_house(ab, x, y)
                if not is_house(g, x, y) or isab:
                    continue
                if not is_house(g, x + 1, y):
                    p2 = rel(p, rot_dir(rot, E), 6)
                    if not is_house(g, x, y + 1):
                        self.add("roof_corner", rel(p2, rot_dir(rot, S), 6), rot)
                    elif is_house(g, x + 1, y + 1):
                        self.add("roof_inner_corner", rel(p2, rot_dir(rot, S), 5), rot)
                    if not is_house(g, x, y - 1):
                        self.add("roof_corner", p2, rot + CCW90)
                    elif is_house(g, x + 1, y - 1):
                        self.add("roof_inner_corner", rel(rel(p, rot_dir(rot, E), 9), rot_dir(rot, N), 2), rot + CW90)
                if is_house(g, x - 1, y):
                    continue
                p2 = rel(rel(p, rot_dir(rot, E), 0), rot_dir(rot, S), 0)
                if not is_house(g, x, y + 1):
                    self.add("roof_corner", rel(p2, rot_dir(rot, S), 6), rot + CW90)
                elif is_house(g, x - 1, y + 1):
                    self.add("roof_inner_corner", rel(rel(p2, rot_dir(rot, S), 8), rot_dir(rot, W), 3), rot + CCW90)
                if not is_house(g, x, y - 1):
                    self.add("roof_corner", p2, rot + CW180)
                    continue
                if not is_house(g, x - 1, y - 1):
                    continue
                self.add("roof_inner_corner", rel(p2, rot_dir(rot, S), 1), rot + CW180)

    def entrance(self, data):
        w = rot_dir(data["rot"], W)
        self.add("entrance", rel(data["pos"], w, 9), data["rot"])
        data["pos"] = rel(data["pos"], rot_dir(data["rot"], S), 16)

    def wall_piece(self, data):
        self.add(data["wall"], rel(data["pos"], rot_dir(data["rot"], E), 7), data["rot"])
        data["pos"] = rel(data["pos"], rot_dir(data["rot"], S), 8)

    def turn(self, data):
        data["pos"] = rel(data["pos"], rot_dir(data["rot"], S), -1)
        self.add("wall_corner", data["pos"], data["rot"])
        data["pos"] = rel(data["pos"], rot_dir(data["rot"], S), -7)
        data["pos"] = rel(data["pos"], rot_dir(data["rot"], W), -6)
        data["rot"] = (data["rot"] + CW90) & 3

    def inner_turn(self, data):
        data["pos"] = rel(data["pos"], rot_dir(data["rot"], S), 6)
        data["pos"] = rel(data["pos"], rot_dir(data["rot"], E), 8)
        data["rot"] = (data["rot"] + CCW90) & 3

    def room1x1(self, rp, rot, dd, rooms):
        r = self.r
        pr = NONE
        name = rooms.r1x1(r)
        if dd != E:
            if dd == N:
                pr = CCW90
            elif dd == W:
                pr = CW180
            elif dd == S:
                pr = CW90
            else:
                name = rooms.r1x1s(r)
        ox, oz = _zero_pos(1, 0, M_NONE, pr, 7, 7)
        pr = (pr + rot) & 3
        ox, oz = rot_pos(rot, ox, oz)
        self.add(name, (rp[0] + ox, rp[1], rp[2] + oz), pr)

    def room1x2(self, rp, rot, rdir, dd, rooms, st):
        r = self.r
        e, s_ = rot_dir(rot, E), rot_dir(rot, S)
        if dd == E and rdir == S:
            self.add(rooms.r1x2side(r, st), rel(rp, e, 1), rot)
        elif dd == E and rdir == N:
            self.add(rooms.r1x2side(r, st), rel(rel(rp, e, 1), s_, 6), rot, M_LR)
        elif dd == W and rdir == N:
            self.add(rooms.r1x2side(r, st), rel(rel(rp, e, 7), s_, 6), rot + CW180)
        elif dd == W and rdir == S:
            self.add(rooms.r1x2side(r, st), rel(rp, e, 7), rot, M_FB)
        elif dd == S and rdir == E:
            self.add(rooms.r1x2side(r, st), rel(rp, e, 1), rot + CW90, M_LR)
        elif dd == S and rdir == W:
            self.add(rooms.r1x2side(r, st), rel(rp, e, 7), rot + CW90)
        elif dd == N and rdir == W:
            self.add(rooms.r1x2side(r, st), rel(rel(rp, e, 7), s_, 6), rot + CW90, M_FB)
        elif dd == N and rdir == E:
            self.add(rooms.r1x2side(r, st), rel(rel(rp, e, 1), s_, 6), rot + CCW90)
        elif dd == S and rdir == N:
            self.add(rooms.r1x2front(r, st), rel(rel(rp, e, 1), rot_dir(rot, N), 8), rot)
        elif dd == N and rdir == S:
            self.add(rooms.r1x2front(r, st), rel(rel(rp, e, 7), s_, 14), rot + CW180)
        elif dd == W and rdir == E:
            self.add(rooms.r1x2front(r, st), rel(rp, e, 15), rot + CW90)
        elif dd == E and rdir == W:
            self.add(rooms.r1x2front(r, st), rel(rel(rp, rot_dir(rot, W), 7), s_, 6), rot + CCW90)
        elif dd == UP and rdir == E:
            self.add(rooms.r1x2s(r), rel(rp, e, 15), rot + CW90)
        elif dd == UP and rdir == S:
            self.add(rooms.r1x2s(r), rel(rel(rp, e, 1), rot_dir(rot, N), 0), rot)

    def room2x2(self, rp, rot, rdir, dd, rooms):
        east = south = 0
        rr = rot
        mir = M_NONE
        if dd == E and rdir == S:
            east = -7
        elif dd == E and rdir == N:
            east, south, mir = -7, 6, M_LR
        elif dd == N and rdir == E:
            east, south, rr = 1, 14, rot + CCW90
        elif dd == N and rdir == W:
            east, south, rr, mir = 7, 14, rot + CCW90, M_LR
        elif dd == S and rdir == W:
            east, south, rr = 7, -8, rot + CW90
        elif dd == S and rdir == E:
            east, south, rr, mir = 1, -8, rot + CW90, M_LR
        elif dd == W and rdir == N:
            east, south, rr = 15, 6, rot + CW180
        elif dd == W and rdir == S:
            east, mir = 15, M_FB
        p = rel(rel(rp, rot_dir(rot, E), east), rot_dir(rot, S), south)
        self.add(rooms.r2x2(self.r), p, rr, mir)


# ── the start ──
def _height(seed, mc, x, z):
    """getFirstOccupiedHeight(WORLD_SURFACE_WG)."""
    mc = int(mc)
    if mc >= 22:
        import terrain
        v = terrain.first_occupied(seed, mc, x, z)
        return None if v is None else int(v)
    import ctypes as ct
    from cubiomes_wrap import get_lib
    f = get_lib()._lib.ssa_t116_base_height
    f.restype = ct.c_int
    v = f(mc, ct.c_uint64(int(seed) & ((1 << 64) - 1)), x, z)
    return None if v < 0 else v - 1


_LAY = {}
OLD_1X1B = 28              # (30/10) « 1x1_b » rooms: 4 up to 1.21.5 (servers), 5 from 1.21.11


def layout(seed, mc, cx, cz):
    """(origin, rotation, [(template, pos, rot, mirror)]) of the mansion started in
    chunk (cx, cz), or None when its ground is under Y 60."""
    k = (int(seed), int(mc), cx, cz)
    if k in _LAY:
        return _LAY[k]
    r = ms.Lcg()
    ms.large_feature_seed(r, int(seed) & ms.M64, cx, cz)
    rot = r.next_int(4)
    i = j = 5
    if rot == CW90:
        i = -5
    elif rot == CW180:
        i, j = -5, -5
    elif rot == CCW90:
        j = -5
    x, z = (cx << 4) + 7, (cz << 4) + 7
    hs = [_height(seed, mc, a, b) for a, b in ((x, z), (x, z + j), (x + i, z), (x + i, z + j))]
    if any(h is None for h in hs):
        return None
    y = min(hs)
    if y < 60:
        _LAY[k] = None
        return None
    origin = (x, y, z)
    if int(mc) <= 22:          # 1.16 → 1.18.2: the pieces from (x0 + 8, Y + 1, z0 + 8)
        origin = (x + 1, y + 1, z + 1)
    m = MansionGrid(r)
    pl = Placer(r, old=int(mc) <= OLD_1X1B)
    pl.create(origin, rot, m)
    res = (origin, rot, pl.pieces)
    if len(_LAY) > 64:
        _LAY.clear()
    _LAY[k] = res
    return res


# ── templates, boxes, chests ──
_T = None


def templates(mc):
    global _T
    if _T is None:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loot_tables", "woodland_mansion.json.gz")
        with gzip.open(p, "rt") as f:
            _T = json.load(f)
    return _T["116" if int(mc) <= 22 else "119"]


def transform(lx, lz, rot, mirror):
    if mirror == M_LR:
        lz = -lz
    elif mirror == M_FB:
        lx = -lx
    return rot_pos(rot, lx, lz)


def world(piece, lx, ly, lz):
    _n, pos, rot, mir = piece
    a, b = transform(lx, lz, rot, mir)
    return pos[0] + a, pos[1] + ly, pos[2] + b


def box(piece, size):
    sx, sy, sz = size
    a = world(piece, 0, 0, 0)
    b = world(piece, sx - 1, sy - 1, sz - 1)
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]), max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]))


def chest_list(seed, mc, cx, cz):
    """[(x, y, z, loot seed, room template)] of the mansion's loot chests."""
    import scattered_loot as sc
    lay = layout(seed, mc, cx, cz)
    if lay is None:
        return []
    T = templates(mc)
    ps = lay[2]
    boxes = [box(p, T[p[0]]["size"]) for p in ps]
    marks = []
    for p in ps:
        for m in T[p[0]]["marks"]:
            marks.append((world(p, m[0], m[1], m[2]), p[0]))
    seeds = {}
    for ch in sorted({(w[0] >> 4, w[2] >> 4) for w, _n in marks}):
        x0, z0 = ch[0] << 4, ch[1] << 4
        r = sc._rng(seed, mc, ch[0], ch[1], "mansion")
        placed = set()
        for p, b in zip(ps, boxes):
            if b[3] < x0 or b[0] > x0 + 15 or b[5] < z0 or b[2] > z0 + 15:
                continue
            d = T[p[0]]
            for c in d["cont"]:
                w = world(p, *c)
                if (w[0] >> 4, w[2] >> 4) == ch:
                    sc._next_long(r)
                    placed.add(w)
            for m in d["marks"]:
                w = world(p, m[0], m[1], m[2])
                if (w[0] >> 4, w[2] >> 4) == ch and w not in placed:
                    seeds[w] = sc._next_long(r)
                    placed.add(w)
    return [(w[0], w[1], w[2], seeds.get(w), n) for w, n in marks]


def chests(seed, mc, x, z, ver):
    import loot_engine as le
    cx, cz = int(x) >> 4, int(z) >> 4
    lst = chest_list(seed, mc, cx, cz)
    if not lst:
        return [dict(name="No chest", pos=(int(x), None, int(z)), table="", seed=None, slots=None, approx=[],
                     note="No loot chest found in this mansion (or its ground is under Y 60: no mansion).")]
    out = []
    oy = layout(seed, mc, cx, cz)[0][1]
    for i, (X, Y, Z, s, n) in enumerate(sorted(lst, key=lambda t: (t[1], t[0], t[2]))):
        slots, approx = le.fill_chest("woodland_mansion", s, ver)
        fl = min(3, max(1, (Y - oy) // 8 + 1))
        secret = "s" in n.split("_")[1] and not n.endswith("_stairs")
        out.append(dict(name=f"Chest {i + 1} · floor {fl}", pos=(X, Y, Z), table="chests/woodland_mansion",
                        seed=s, slots=slots, approx=approx,
                        note=f"Room {n}" + (" (a secret room: no door, behind a wall)" if secret else "") + "."))
    return out


def map_boxes(seed, mc, cx, cz):
    """[(x0, z0, x1, z1, kind, y0, y1)] of the rooms (and the corridors) for the map."""
    lay = layout(seed, mc, cx, cz)
    if lay is None:
        return []
    T = templates(mc)
    out = []
    for p in lay[2]:
        n = p[0]
        if n.startswith(("1x1", "1x2", "2x2")):
            kind = "house"
        elif n == "corridor_floor":
            kind = "street"
        elif n == "entrance":
            kind = "center"
        else:
            continue
        b = box(p, T[n]["size"])
        out.append((b[0], b[2], b[3], b[5], kind, b[1], b[4]))
    return out
