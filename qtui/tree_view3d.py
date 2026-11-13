"""
qtui/tree_view3d.py — (05/11) the 3D scene of the Seedcracking page: the population
chunks and the trees one entered, drawn block by block (OpenGL 3.3, procedural
textures — no game asset), with the 12 random corner leaves of each oak / birch
shown as what they are:

    leaf      a leaf block (seen)
    no leaf   a thin white frame (seen empty)
    unknown   a translucent red-and-white target block

Mouse: left-click a tree to select it, a corner to cycle leaf → no leaf → unknown,
the ground to place a tree (Add tool, or double-click). Left-drag turns the view,
right-drag (or Shift + left) pans, the wheel zooms. Arrow keys move the selected
tree, Delete removes it.
"""
from __future__ import annotations

import math
import random

import numpy as np
from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QImage, QMatrix4x4, QPainter, QPen, QSurfaceFormat,
                           QVector3D, QVector4D)
from PySide6.QtOpenGL import (QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram, QOpenGLTexture,
                              QOpenGLVertexArrayObject)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from . import theme

GL_TRIANGLES, GL_LINES = 0x0004, 0x0001
GL_DEPTH_TEST, GL_BLEND, GL_CULL_FACE = 0x0B71, 0x0BE2, 0x0B44
GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA = 0x0302, 0x0303
GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT = 0x4000, 0x0100
GL_FLOAT, GL_FALSE = 0x1406, 0
GL_LEQUAL = 0x0203

GROUND_Y = 64                      # trees stand on y = 64 (top of the grass at 63)

# ── procedural textures (16 × 16, an atlas of 4 × 4 tiles) ─────────────────────
T_GRASS, T_DIRT, T_GRASS_SIDE, T_OAK_SIDE, T_OAK_TOP, T_BIRCH_SIDE, T_BIRCH_TOP, \
    T_LEAVES, T_TARGET, T_WHITE, T_FANCY_LEAVES, T_MARK, T_SPRUCE_SIDE, T_SPRUCE_TOP, \
    T_NEEDLES = range(15)


def _atlas() -> QImage:
    rnd = random.Random(1234)
    img = QImage(64, 64, QImage.Format.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))

    def tile(i, fn):
        ox, oy = (i % 4) * 16, (i // 4) * 16
        for y in range(16):
            for x in range(16):
                c = fn(x, y)
                img.setPixelColor(ox + x, oy + y, QColor(*c))

    def noise(base, var):
        def f(x, y):
            k = rnd.uniform(-var, var)
            return tuple(max(0, min(255, int(v * (1 + k)))) for v in base) + (255,)
        return f

    tile(T_GRASS, noise((100, 156, 64), 0.12))
    tile(T_DIRT, noise((134, 96, 67), 0.15))

    def gside(x, y):
        edge = 3 + (1 if (x * 7) % 5 == 0 else 0)
        return noise((112, 168, 74) if y < edge else (134, 96, 67), 0.13)(x, y)
    tile(T_GRASS_SIDE, gside)

    def oak_side(x, y):
        stripe = (x % 4 == 0) or ((x + y // 5) % 7 == 3)
        base = (88, 66, 40) if stripe else (109, 84, 51)
        return noise(base, 0.08)(x, y)
    tile(T_OAK_SIDE, oak_side)

    def rings(c1, c2, bark):
        def f(x, y):
            d = max(abs(x - 7.5), abs(y - 7.5))
            if d > 6.5:
                return noise(bark, 0.08)(x, y)
            return noise(c1 if int(d) % 2 == 0 else c2, 0.05)(x, y)
        return f
    tile(T_OAK_TOP, rings((176, 141, 87), (152, 119, 70), (109, 84, 51)))

    spots = {(rnd.randrange(16), rnd.randrange(16)) for _ in range(14)}

    def birch_side(x, y):
        if (x, y) in spots or (x + 1, y) in spots:
            return noise((48, 46, 42), 0.1)(x, y)
        return noise((218, 214, 203), 0.04)(x, y)
    tile(T_BIRCH_SIDE, birch_side)
    tile(T_BIRCH_TOP, rings((200, 180, 128), (178, 158, 108), (218, 214, 203)))

    def leaves(x, y):
        r = rnd.random()
        if r < 0.16:
            return (110, 110, 110, 255)
        v = rnd.randint(150, 230)
        return (v, v, v, 255)
    tile(T_LEAVES, leaves)
    tile(T_FANCY_LEAVES, leaves)

    def target(x, y):
        d = max(abs(x - 7.5), abs(y - 7.5))
        ring = int(d + 0.5)
        red = ring in (0, 1, 4, 5) or ring >= 7 and False
        if ring == 7 or ring == 8:
            red = True
        return (214, 58, 52, 255) if red else (238, 228, 222, 255)
    tile(T_TARGET, target)
    tile(T_WHITE, lambda x, y: (255, 255, 255, 255))

    def mark(x, y):                         # "?" marker of an unknown-type tree
        return (120, 120, 128, 255)
    tile(T_MARK, mark)

    def spruce_side(x, y):                  # (06/11) dark, rough bark
        stripe = (x % 3 == 0) or ((x * 5 + y // 3) % 7 == 2)
        return noise((58, 40, 26) if stripe else (79, 56, 34), 0.09)(x, y)
    tile(T_SPRUCE_SIDE, spruce_side)
    tile(T_SPRUCE_TOP, rings((120, 92, 58), (100, 74, 44), (79, 56, 34)))

    def needles(x, y):                      # denser than the leaves
        r = rnd.random()
        if r < 0.24:
            return (90, 90, 90, 255)
        v = rnd.randint(140, 215)
        return (v, v, v, 255)
    tile(T_NEEDLES, needles)
    return img


def _uv(tile):
    ox, oy = (tile % 4) * 0.25, (tile // 4) * 0.25
    e = 0.25 / 64                           # half a texel inside the tile
    return ox + e, oy + e, ox + 0.25 - e, oy + 0.25 - e


# faces: (normal, 4 corners in order, shade)
_FACES = (
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0)), 1.00),     # top
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)), 0.50),    # bottom
    ((0, 0, -1), ((1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0)), 0.80),    # north
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)), 0.80),     # south
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)), 0.62),    # west
    ((1, 0, 0), ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1)), 0.62),     # east
)

LEAF_COL = {"oak": (0.42, 0.66, 0.27), "birch": (0.53, 0.70, 0.33), "fancy": (0.40, 0.63, 0.26),
            "spruce": (0.30, 0.47, 0.32), "pine": (0.34, 0.52, 0.32)}

_VS = """#version 330 core
layout(location = 0) in vec3 pos;
layout(location = 1) in vec2 uv;
layout(location = 2) in vec4 col;
uniform mat4 mvp;
uniform vec3 eye;
out vec2 v_uv; out vec4 v_col; out float v_dist;
void main() {
    gl_Position = mvp * vec4(pos, 1.0);
    v_uv = uv; v_col = col; v_dist = length(pos - eye);
}"""
_FS = """#version 330 core
in vec2 v_uv; in vec4 v_col; in float v_dist;
uniform sampler2D atlas;
uniform vec3 fog_col; uniform float fog_near; uniform float fog_far;
out vec4 frag;
void main() {
    vec4 t = texture(atlas, v_uv) * v_col;
    if (t.a < 0.02) discard;
    float f = clamp((v_dist - fog_near) / (fog_far - fog_near), 0.0, 1.0);
    frag = vec4(mix(t.rgb, fog_col, f * 0.85), t.a);
}"""


# ── what the scene looks like, block by block ─────────────────────────────────
def _row(out, x0, y, z0, r, ti, center_free):
    """a leaf layer of radius r, square minus its corners (spruce / pine foliage)"""
    for dx in range(-r, r + 1):
        for dz in range(-r, r + 1):
            if r > 0 and abs(dx) == r and abs(dz) == r:
                continue
            if dx == 0 and dz == 0 and not center_free:
                continue
            out.append((x0 + dx, y, z0 + dz, "leaf", (ti, -1, -1)))


def conifer_blocks(t, ti):
    """(06/11) a spruce or a pine as the game builds it (1.16+ foliage placers), from
    what was entered — typical values where it is unknown."""
    out = []
    x0, z0, y0 = t.x, t.z, GROUND_Y
    a = t.attrs or {}
    if t.type == "spruce":
        h = t.height or 7
        bare = a.get("bare", 1 if h <= 5 else 2)
        fh = max(1, h - bare)
        off = a.get("above", 2) - 1
        top = a.get("top", 0)
        rad = a.get("widest", 3 if fh + off + 1 + top >= 8 else 2)
        i, j, k = top, 1, 0
        for l in range(off, -fh - 1, -1):          # from the tip down
            y = y0 + h + l
            _row(out, x0, y, z0, i, ti, y >= y0 + h)
            if i >= j:
                i, k, j = k, 1, min(j + 1, rad)
            else:
                i += 1
    else:
        h = t.height or 8
        layers = a.get("layers", 4)
        w = a.get("widest", 2)
        fh = layers if w == 1 else layers - 1
        fr = w
        i, off = 0, 1
        for j in range(off, off - fh - 1, -1):
            y = y0 + h + j
            _row(out, x0, y, z0, i, ti, y >= y0 + h)
            if i >= 1 and j == off - fh + 1:
                i -= 1
            elif i < fr:
                i += 1
    for y in range(h):
        out.append((x0, y0 + y, z0, "log", (ti, -1, -1)))
    return out


def tree_blocks(t, ti, ghost=False):
    """[(x, y, z, kind, info)] of one tree. kind: log, leaf, corner_on, corner_off,
    corner_unk, mark; info = (tree index, layer, corner) for corners."""
    out = []
    x0, z0, y0 = t.x, t.z, GROUND_Y
    typ = t.type
    if typ == "unknown":
        h = t.height or 5
        for y in range(h):
            out.append((x0, y0 + y, z0, "log_unknown", (ti, -1, -1)))
        out.append((x0, y0 + h, z0, "mark", (ti, -1, -1)))
        return out
    if typ in ("spruce", "pine"):
        return conifer_blocks(t, ti)
    if typ == "fancy":
        h = t.height or 7
        for y in range(h):
            out.append((x0, y0 + y, z0, "log", (ti, -1, -1)))
        cy = y0 + h
        for dx in range(-2, 3):
            for dy in range(-1, 3):
                for dz in range(-2, 3):
                    if dx * dx + dz * dz + (dy - 0.5) ** 2 * 1.4 <= 6.2 and not (dx == dz == 0 and dy < 0):
                        out.append((x0 + dx, cy + dy, z0 + dz, "leaf", (ti, -1, -1)))
        return out
    lo, hi = (4, 6) if typ == "oak" else (5, 7)
    h = t.height if t.height is not None else (lo + hi) // 2
    top = y0 + h - 1
    for y in range(h):
        out.append((x0, y0 + y, z0, "log", (ti, -1, -1)))
    # layers top-1 .. top+1 (relative): radius 2, 2, 1, 1
    for layer_y, r, li in ((top - 2, 2, 0), (top - 1, 2, 1), (top, 1, 2), (top + 1, 1, None)):
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                if dx == 0 and dz == 0 and layer_y <= top:
                    continue
                corner = abs(dx) == r and abs(dz) == r
                if corner and li is None:
                    continue                # the top layer never keeps its corners
                if corner:
                    ci = (0 if dz < 0 else 1) if dx < 0 else (2 if dz < 0 else 3)
                    st = t.leaves[li * 4 + ci]
                    kind = {"1": "corner_on", "0": "corner_off"}.get(st, "corner_unk")
                    out.append((x0 + dx, layer_y, z0 + dz, kind, (ti, li, ci)))
                else:
                    out.append((x0 + dx, layer_y, z0 + dz, "leaf", (ti, -1, -1)))
    return out


class TreeView3D(QOpenGLWidget):
    treeSelected = Signal(int)                 # index in scene.trees, -1 = none
    cornerClicked = Signal(int, int, int)      # tree, layer, corner
    groundClicked = Signal(int, int)           # block x, z (place a tree)
    hoverText = Signal(str)
    moveSelected = Signal(int, int)            # dx, dz (arrow keys)
    deleteSelected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.scene = None
        self.selected = -1
        self.ghosts = []                       # predicted trees (Tree), drawn translucent
        self.ghost_chunk = None
        self.tool = "select"
        self.isolate = False                   # only the selected tree is solid / clickable
        self.focus_chunk = (0, 0)
        self.yaw, self.pitch, self.dist = -35.0, 38.0, 42.0
        self.target = QVector3D(8, GROUND_Y + 3, 8)
        self._press = None
        self._drag = None
        self._moved = False
        self._hover = None                     # (x, y, z) or ground (x, z)
        self._pick = {}
        self._solid = set()
        self._gl_ok = None
        self._gl_err = ""
        self._dirty = True
        self._n_opaque = self._n_trans = self._n_lines = 0
        self._chunk_labels = []

    # ── API ──────────────────────────────────────────────────────────────────
    def set_scene(self, scene, selected=-1, reframe=False):
        self.scene = scene
        self.selected = selected
        if reframe:
            self.reframe()
        self._dirty = True
        self.update()

    def set_selected(self, i):
        self.selected = i
        self._dirty = True
        self.update()

    def set_ghosts(self, trees, chunk=None):
        self.ghosts = list(trees or [])
        self.ghost_chunk = chunk
        self._dirty = True
        self.update()

    def set_tool(self, tool):
        self.tool = tool
        self.setCursor(Qt.CursorShape.CrossCursor if tool == "add" else Qt.CursorShape.ArrowCursor)

    def reframe(self):
        """Frame the trees near the selected one (or the biggest group): trees far
        apart (other places of the world) are not framed together."""
        trees = self.scene.trees if self.scene else []
        if trees:
            ref = trees[self.selected] if 0 <= self.selected < len(trees) else None
            groups = []
            for t in trees:                      # trees within 48 blocks of one another
                for g in groups:
                    if any(abs(t.x - o.x) <= 48 and abs(t.z - o.z) <= 48 for o in g):
                        g.append(t)
                        break
                else:
                    groups.append([t])
            grp = next((g for g in groups if ref in g), None) if ref is not None else None
            grp = grp or max(groups, key=len)
            xs, zs = [t.x for t in grp], [t.z for t in grp]
            cx, cz = (min(xs) + max(xs)) / 2 + 0.5, (min(zs) + max(zs)) / 2 + 0.5
            span = max(max(xs) - min(xs), max(zs) - min(zs), 16)
        else:
            ch = self._target_chunk()              # no tree: stay where the camera is
            cx, cz = ch[0] * 16 + 8, ch[1] * 16 + 8
            span = 16
        self.target = QVector3D(cx, GROUND_Y + 3, cz)
        self.dist = max(22.0, min(260.0, span * 1.7))
        self.update()

    def follow(self, x, z):
        """Bring (x, z) into view if it left it (the camera keeps its angle)."""
        tx, tz = self.target.x(), self.target.z()
        if abs(x + 0.5 - tx) > self.dist * 0.45 or abs(z + 0.5 - tz) > self.dist * 0.45:
            self.target = QVector3D(x + 0.5, GROUND_Y + 3, z + 0.5)
            self.dist = min(self.dist, 60.0)
            self.update()

    def face_north(self):
        self.yaw = 0.0
        self.update()

    def view_top(self):
        self.pitch = 89.0
        self.yaw = 0.0
        self.update()

    def view_iso(self):
        self.pitch, self.yaw = 38.0, -35.0
        self.update()

    def gl_status(self):
        return self._gl_ok, self._gl_err

    # ── geometry ─────────────────────────────────────────────────────────────
    def _chunks(self):
        ch = set()
        if self.scene:
            for t in self.scene.trees:
                ch.add((t.x >> 4, t.z >> 4))
        if not ch:
            # (06/11) no tree: the chunk the camera looks at (deleting every tree
            # left the camera over empty space, the ground back at the old focus)
            ch.add(self._target_chunk())
        if self.ghost_chunk is not None:
            ch.add(tuple(self.ghost_chunk))
        return ch

    def _target_chunk(self):
        return (math.floor(self.target.x()) >> 4, math.floor(self.target.z()) >> 4)

    def _build(self):
        used = self._chunks()
        # (06/11) the ground: the used chunks and the ring around each — a set, not a
        # bounding box (two trees thousands of blocks apart made millions of blocks)
        ground = {(c[0] + dx, c[1] + dz) for c in used for dx in (-1, 0, 1) for dz in (-1, 0, 1)}
        self._ground = ground
        blocks = []
        pick = {}
        faded = []
        iso = self.isolate and 0 <= self.selected < (len(self.scene.trees) if self.scene else 0)
        if self.scene:
            for i, t in enumerate(self.scene.trees):
                for b in tree_blocks(t, i):
                    if iso and i != self.selected:
                        faded.append(b)
                        continue
                    blocks.append(b)
                    pick[(b[0], b[1], b[2])] = b
        solid = {(b[0], b[1], b[2]) for b in blocks if b[3] in ("log", "leaf", "corner_on", "log_unknown", "mark")}
        self._pick, self._solid = pick, solid
        op, tr, ln = [], [], []
        sel = self.selected

        def occ(x, y, z):
            if (x, y, z) in solid:
                return 1
            return 1 if (y == GROUND_Y - 1 and (x >> 4, z >> 4) in ground) else 0

        AO = (1.0, 0.80, 0.66, 0.52)

        def quad(out, x, y, z, face, tile, col, ao=True):
            (nx, ny, nz), corners, shade = face
            u0, v0, u1, v1 = _uv(tile)
            uvs = ((u0, v1), (u1, v1), (u1, v0), (u0, v0))
            r, g, b, a = col
            lv = [0, 0, 0, 0]
            if ao:
                px, py, pz = x + nx, y + ny, z + nz
                for k, c in enumerate(corners):
                    # the two tangent axes and the side the corner is on
                    d = [c[0] * 2 - 1, c[1] * 2 - 1, c[2] * 2 - 1]
                    t = [i for i, nn in enumerate((nx, ny, nz)) if nn == 0]
                    o1 = [0, 0, 0]; o1[t[0]] = d[t[0]]
                    o2 = [0, 0, 0]; o2[t[1]] = d[t[1]]
                    s1 = occ(px + o1[0], py + o1[1], pz + o1[2])
                    s2 = occ(px + o2[0], py + o2[1], pz + o2[2])
                    cc = occ(px + o1[0] + o2[0], py + o1[1] + o2[1], pz + o1[2] + o2[2])
                    lv[k] = 3 if (s1 and s2) else s1 + s2 + cc
            vs = [(x + cx, y + cy, z + cz, uvs[k][0], uvs[k][1],
                   r * shade * AO[lv[k]], g * shade * AO[lv[k]], b * shade * AO[lv[k]], a)
                  for k, (cx, cy, cz) in enumerate(corners)]
            if lv[0] + lv[2] > lv[1] + lv[3]:          # split along the brighter diagonal
                out.extend((vs[1], vs[2], vs[3], vs[1], vs[3], vs[0]))
            else:
                out.extend((vs[0], vs[1], vs[2], vs[0], vs[2], vs[3]))

        def cube(out, x, y, z, tiles, col, cull=True):
            for fi, face in enumerate(_FACES):
                n = face[0]
                if cull and (x + n[0], y + n[1], z + n[2]) in solid:
                    continue
                tile = tiles[0] if fi == 0 else tiles[1] if fi == 1 else tiles[2]
                quad(out, x, y, z, face, tile, col, ao=cull)

        def box_lines(x, y, z, col, inset=0.0):
            a, b = inset, 1 - inset
            P = [(x + i, y + j, z + k) for i in (a, b) for j in (a, b) for k in (a, b)]
            for i, j in ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7),
                         (0, 4), (1, 5), (2, 6), (3, 7)):
                u0, v0, _u1, _v1 = _uv(T_WHITE)
                for p in (P[i], P[j]):
                    ln.append((p[0], p[1], p[2], u0, v0) + col)

        # ground: grass of the chunks, the used ones bright, the ring dimmer
        for (cx, cz) in ground:
            on = (cx, cz) in used
            k = 1.0 if on else 0.58
            for bx in range(cx * 16, cx * 16 + 16):
                for bz in range(cz * 16, cz * 16 + 16):
                    chk = 0.96 if (bx + bz) & 1 else 1.0
                    quad(op, bx, GROUND_Y - 1, bz, _FACES[0], T_GRASS, (k * chk, k * chk, k * chk, 1.0),
                         ao=on)
            # the ground's edge where the next chunk is not drawn
            for (nx, nz), face, side in (((0, -1), _FACES[2], "n"), ((0, 1), _FACES[3], "s"),
                                         ((-1, 0), _FACES[4], "w"), ((1, 0), _FACES[5], "e")):
                if (cx + nx, cz + nz) in ground:
                    continue
                for i in range(16):
                    bx = cx * 16 + (i if side in "ns" else (0 if side == "w" else 15))
                    bz = cz * 16 + (i if side in "we" else (0 if side == "n" else 15))
                    quad(op, bx, GROUND_Y - 1, bz, face, T_GRASS_SIDE, (0.8, 0.8, 0.8, 1), ao=False)
                    quad(op, bx, GROUND_Y - 2, bz, face, T_DIRT, (0.7, 0.7, 0.7, 1), ao=False)
        # chunk borders (each edge once)
        u0, v0, _u, _v = _uv(T_WHITE)
        yb = GROUND_Y + 0.02
        acc = QColor(theme.ACCENT or "#5aa0ff")
        edges = {}
        for (cx, cz) in ground:
            for e in (((cx, cz), (cx + 1, cz)), ((cx, cz + 1), (cx + 1, cz + 1)),
                      ((cx, cz), (cx, cz + 1)), ((cx + 1, cz), (cx + 1, cz + 1))):
                edges[e] = edges.get(e, False) or (cx, cz) in used
        for ((ax, az), (bx_, bz_)), on in edges.items():
            col = ((acc.redF(), acc.greenF(), acc.blueF(), 0.95) if on else (1, 1, 1, 0.28))
            ln.append((ax * 16, yb, az * 16, u0, v0) + col)
            ln.append((bx_ * 16, yb, bz_ * 16, u0, v0) + col)
        # corners seen with a leaf that another tree may have placed
        risky = set()
        if self.scene:
            try:
                import tree_crack as _tc
                for i, t in enumerate(self.scene.trees):
                    for li, ci in _tc.risky_corners(self.scene, t):
                        risky.add((i, li, ci))
            except Exception:
                pass
        # trees
        for (x, y, z, kind, info) in blocks:
            ti = info[0]
            t = self.scene.trees[ti]
            hl = 1.18 if ti == sel else 1.0
            if kind in ("log", "log_unknown"):
                side, top = ((T_BIRCH_SIDE, T_BIRCH_TOP) if t.type == "birch" else
                             (T_SPRUCE_SIDE, T_SPRUCE_TOP) if t.type in ("spruce", "pine") else
                             (T_OAK_SIDE, T_OAK_TOP))
                c = (0.75, 0.75, 0.78, 1) if kind == "log_unknown" else (min(1, hl), min(1, hl), min(1, hl), 1)
                cube(op, x, y, z, (top, top, side), c)
            elif kind == "mark":
                cube(op, x, y, z, (T_TARGET, T_TARGET, T_TARGET), (0.9, 0.9, 0.95, 1))
            elif kind in ("leaf", "corner_on"):
                r, g, b = LEAF_COL.get(t.type, LEAF_COL["oak"])
                if kind == "corner_on" and ti == sel:
                    r, g, b = r * 1.1, g * 1.25, b * 1.0
                lt = T_NEEDLES if t.type in ("spruce", "pine") else T_LEAVES
                cube(op, x, y, z, (lt, lt, lt), (min(1, r * hl), min(1, g * hl), min(1, b * hl), 1))
                if kind == "corner_on" and info in risky:
                    box_lines(x, y, z, (1.0, 0.72, 0.18, 1.0), -0.006)
                elif kind == "corner_on" and ti == sel:
                    box_lines(x, y, z, (0.86, 1.0, 0.62, 0.9), -0.004)
            elif kind == "corner_unk":
                cube(tr, x, y, z, (T_TARGET, T_TARGET, T_TARGET), (1, 1, 1, 0.78 if ti == sel else 0.55), cull=False)
            elif kind == "corner_off":
                a = 0.95 if ti == sel else 0.55
                box_lines(x, y, z, (1, 1, 1, a), 0.06)
        # the selected tree: an outline at its foot
        if 0 <= sel < (len(self.scene.trees) if self.scene else 0):
            t = self.scene.trees[sel]
            for (ax, az, bx, bz) in ((0, 0, 1, 0), (1, 0, 1, 1), (1, 1, 0, 1), (0, 1, 0, 0)):
                ln.append((t.x + ax, yb + 0.01, t.z + az, u0, v0) + (1, 0.85, 0.3, 1))
                ln.append((t.x + bx, yb + 0.01, t.z + bz, u0, v0) + (1, 0.85, 0.3, 1))
        # the other trees while one is isolated: faint, not clickable
        fsolid = {(b[0], b[1], b[2]) for b in faded}
        for (x, y, z, kind, info) in faded:
            if kind == "corner_off":
                continue
            for fi, face in enumerate(_FACES):
                n = face[0]
                if (x + n[0], y + n[1], z + n[2]) in fsolid:
                    continue
                quad(tr, x, y, z, face, T_WHITE, (0.75, 0.8, 0.75, 0.10), ao=False)
        # predicted trees (compare): a translucent "hologram", outer faces only
        gblocks = []
        for g in self.ghosts:
            gblocks += [(x, y, z, kind, g) for (x, y, z, kind, _i) in tree_blocks(g, -1)]
        gsolid = {(b[0], b[1], b[2]) for b in gblocks}
        for (x, y, z, kind, g) in gblocks:
            log = kind in ("log", "log_unknown")
            col = (0.95, 0.80, 0.55, 0.55) if log else (0.55, 0.85, 1.0, 0.30)
            tiles = (T_OAK_TOP, T_OAK_TOP, T_OAK_SIDE) if log else (T_WHITE, T_WHITE, T_WHITE)
            for fi, face in enumerate(_FACES):
                n = face[0]
                if (x + n[0], y + n[1], z + n[2]) in gsolid:
                    continue
                quad(tr, x, y, z, face, tiles[0] if fi < 2 else tiles[2], col, ao=False)
            if kind in ("corner_on",):
                box_lines(x, y, z, (0.7, 0.95, 1.0, 0.8), -0.003)
        # chunk labels (drawn by QPainter)
        self._chunk_labels = []
        for (cx, cz) in sorted(used):
            bio = self.scene.biome((cx, cz)) if self.scene else "forest"
            n = sum(1 for t in (self.scene.trees if self.scene else []) if (t.x >> 4, t.z >> 4) == (cx, cz))
            self._chunk_labels.append((QVector3D(cx * 16 + 0.3, GROUND_Y + 0.05, cz * 16 + 0.3),
                                       f"Chunk {cx}, {cz}", f"{ {'birch_forest': 'Birch Forest', 'taiga': 'Taiga'}.get(bio, 'Forest') }"
                                                            f" · {n} tree{'s' * (n != 1)}"))
        return (np.array(op, dtype=np.float32).reshape(-1, 9) if op else np.zeros((0, 9), np.float32),
                np.array(tr, dtype=np.float32).reshape(-1, 9) if tr else np.zeros((0, 9), np.float32),
                np.array(ln, dtype=np.float32).reshape(-1, 9) if ln else np.zeros((0, 9), np.float32))

    # ── GL ───────────────────────────────────────────────────────────────────
    def _cleanup(self):
        """(06/11) free the GL objects while the context is still current — destroying
        them later (no context) can crash the NVIDIA driver at exit."""
        if not getattr(self, "_gl_ok", None):
            return
        try:
            self.makeCurrent()
            self.tex.destroy()
            for vao in self.vaos:
                vao.destroy()
            for vbo in self.vbos:
                vbo.destroy()
            self.prog.removeAllShaders()
        except Exception:
            pass
        finally:
            self._gl_ok = None
            try:
                self.doneCurrent()
            except Exception:
                pass

    def initializeGL(self):
        try:
            self.context().aboutToBeDestroyed.connect(self._cleanup)
            self.f = self.context().functions()
            self.prog = QOpenGLShaderProgram(self)
            ok = (self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _VS)
                  and self.prog.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _FS)
                  and self.prog.link())
            if not ok:
                raise RuntimeError(self.prog.log())
            self.tex = QOpenGLTexture(_atlas().mirrored(False, False))
            self.tex.setMinificationFilter(QOpenGLTexture.Filter.NearestMipMapLinear)
            self.tex.setMagnificationFilter(QOpenGLTexture.Filter.Nearest)
            self.tex.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
            self.vaos, self.vbos = [], []
            for _ in range(3):
                vao = QOpenGLVertexArrayObject(self); vao.create()
                vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer); vbo.create()
                self.vaos.append(vao); self.vbos.append(vbo)
            self._gl_ok = True
        except Exception as e:
            self._gl_ok = False
            self._gl_err = str(e)

    def _upload(self):
        op, tr, ln = self._build()
        f = self.f
        for k, arr in enumerate((op, tr, ln)):
            vao, vbo = self.vaos[k], self.vbos[k]
            vao.bind()
            vbo.bind()
            data = arr.tobytes()
            vbo.allocate(data, len(data))
            stride = 9 * 4
            for loc, size, off in ((0, 3, 0), (1, 2, 12), (2, 4, 20)):
                f.glEnableVertexAttribArray(loc)
                f.glVertexAttribPointer(loc, size, GL_FLOAT, GL_FALSE, stride, _voidp(off))
            vao.release()
            vbo.release()
        self._n_opaque, self._n_trans, self._n_lines = len(op), len(tr), len(ln)
        self._dirty = False

    def _matrices(self):
        w, h = max(1, self.width()), max(1, self.height())
        proj = QMatrix4x4()
        proj.perspective(48.0, w / h, 0.3, 1200.0)
        yaw, pitch = math.radians(self.yaw), math.radians(self.pitch)
        d = self.dist
        eye = self.target + QVector3D(math.cos(pitch) * math.sin(yaw) * d, math.sin(pitch) * d,
                                      math.cos(pitch) * math.cos(yaw) * d)
        view = QMatrix4x4()
        up = QVector3D(0, 1, 0) if self.pitch < 88.9 else QVector3D(-math.sin(yaw), 0, -math.cos(yaw))
        view.lookAt(eye, self.target, up)
        return proj, view, eye

    def paintGL(self):
        p = QPainter(self)
        try:
            self._paint(p)
        except Exception as e:                  # never leave the painter open
            self._gl_ok = False
            self._gl_err = f"{type(e).__name__}: {e}"
        finally:
            if p.isActive():
                p.end()

    def _paint(self, p):
        if self._gl_ok is None:
            return
        if not self._gl_ok:
            p.fillRect(self.rect(), QColor(theme.BG1 or "#202228"))
            p.setPen(QColor(theme.TEXT2 or "#aaa"))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       "3D view unavailable (OpenGL 3.3 needed)\n" + (self._gl_err or "")[:300])
            return
        p.beginNativePainting()
        f = self.f
        if not (self.scene and self.scene.trees) and self._target_chunk() != getattr(self, "_empty_at", None):
            self._dirty = True                  # empty scene: the ground follows the camera
        if self._dirty:
            self._empty_at = None if (self.scene and self.scene.trees) else self._target_chunk()
            self._upload()
        dpr = self.devicePixelRatioF()
        f.glViewport(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        top = _sky_color()
        f.glClearColor(top.redF(), top.greenF(), top.blueF(), 1.0)
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        f.glEnable(GL_DEPTH_TEST)
        f.glDepthFunc(GL_LEQUAL)
        proj, view, eye = self._matrices()
        self._mvp = proj * view
        self.prog.bind()
        self.prog.setUniformValue("mvp", self._mvp)
        self.prog.setUniformValue("eye", eye)
        self.prog.setUniformValue1i("atlas", 0)
        self.prog.setUniformValue("fog_col", QVector3D(top.redF(), top.greenF(), top.blueF()))
        self.prog.setUniformValue1f("fog_near", float(self.dist * 1.6))
        self.prog.setUniformValue1f("fog_far", float(self.dist * 4.5 + 60))
        self.tex.bind(0)
        f.glEnable(GL_CULL_FACE)
        self.vaos[0].bind(); f.glDrawArrays(GL_TRIANGLES, 0, self._n_opaque); self.vaos[0].release()
        f.glDisable(GL_CULL_FACE)
        f.glEnable(GL_BLEND)
        f.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self.vaos[2].bind(); f.glDrawArrays(GL_LINES, 0, self._n_lines); self.vaos[2].release()
        f.glDepthMask(False)
        self.vaos[1].bind(); f.glDrawArrays(GL_TRIANGLES, 0, self._n_trans); self.vaos[1].release()
        f.glDepthMask(True)
        f.glDisable(GL_BLEND)
        f.glDisable(GL_DEPTH_TEST)
        self.tex.release()
        self.prog.release()
        p.endNativePainting()
        self._overlay(p)

    # ── overlay (QPainter) ───────────────────────────────────────────────────
    def _project(self, v: QVector3D):
        c = self._mvp.map(QVector4D(v.x(), v.y(), v.z(), 1.0))
        if c.w() <= 0.05:
            return None
        x = (c.x() / c.w() * 0.5 + 0.5) * self.width()
        y = (1 - (c.y() / c.w() * 0.5 + 0.5)) * self.height()
        return QPointF(x, y)

    def _overlay(self, p: QPainter):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        f1 = theme.ui_font(9.0, QFont.Weight.DemiBold)
        f2 = theme.ui_font(8.0)
        for v, t1, t2 in self._chunk_labels:
            q = self._project(v)
            if q is None:
                continue
            p.setFont(f1)
            w = max(p.fontMetrics().horizontalAdvance(t1), p.fontMetrics().horizontalAdvance(t2) * 0.9) + 16
            r = QRectF(q.x() + 4, q.y() - 40, w, 36)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(12, 14, 18, 170))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor(255, 255, 255))
            p.drawText(r.adjusted(8, 3, -4, -16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, t1)
            p.setFont(f2)
            p.setPen(QColor(205, 210, 220))
            p.drawText(r.adjusted(8, 18, -4, -2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, t2)
        self._draw_compass(p)
        # legend
        items = (("leaf", QColor(108, 168, 69)), ("no leaf", None), ("unknown", QColor(214, 58, 52)),
                 ("shared?", "amber"))
        p.setFont(f2)
        fm = p.fontMetrics()
        w = sum(fm.horizontalAdvance(t) + 30 for t, _c in items) + 8
        r = QRectF(self.width() - w - 12, self.height() - 34, w, 24)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(12, 14, 18, 160))
        p.drawRoundedRect(r, 6, 6)
        x = r.x() + 10
        for t, c in items:
            sq = QRectF(x, r.y() + 7, 10, 10)
            if c is None:
                p.setPen(QPen(QColor(255, 255, 255), 1.2)); p.setBrush(Qt.BrushStyle.NoBrush)
            elif c == "amber":
                p.setPen(QPen(QColor(255, 184, 46), 1.6)); p.setBrush(QColor(108, 168, 69))
            else:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(c)
            p.drawRect(sq)
            p.setPen(QColor(225, 228, 235))
            p.drawText(QRectF(x + 15, r.y(), fm.horizontalAdvance(t) + 6, r.height()),
                       Qt.AlignmentFlag.AlignVCenter, t)
            x += fm.horizontalAdvance(t) + 30
        if self.tool == "add":
            p.setFont(f1)
            msg = "Click the ground to place a tree  ·  Esc to stop"
            fm = p.fontMetrics()
            rr = QRectF((self.width() - fm.horizontalAdvance(msg) - 24) / 2, 12, fm.horizontalAdvance(msg) + 24, 28)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.ACCENT or "#4b8bff"))
            p.drawRoundedRect(rr, 8, 8)
            p.setPen(QColor(255, 255, 255)); p.drawText(rr, Qt.AlignmentFlag.AlignCenter, msg)
        if self._hover is not None and len(self._hover) == 3:     # block under the mouse
            hx, hy, hz = self._hover
            P = [self._project(QVector3D(hx + i, hy + j, hz + k)) for i in (0, 1) for j in (0, 1) for k in (0, 1)]
            if all(P):
                p.setPen(QPen(QColor(255, 236, 140, 235), 2.0))
                for i, j in ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7),
                             (0, 4), (1, 5), (2, 6), (3, 7)):
                    p.drawLine(P[i], P[j])
        if self._hover is not None and len(self._hover) == 2:     # ground cell under the mouse
            hx, hz = self._hover
            pts = [self._project(QVector3D(hx + a, GROUND_Y + 0.03, hz + b)) for a, b in ((0, 0), (1, 0), (1, 1), (0, 1))]
            if all(pts):
                p.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
                p.setBrush(QColor(255, 255, 255, 40))
                from PySide6.QtGui import QPolygonF
                p.drawPolygon(QPolygonF(pts))

    def _compass_geo(self):
        R = 36.0
        return QPointF(self.width() - R - 14, R + 14), R

    def _draw_compass(self, p: QPainter):
        """A compass dial: N (red) / E / S / W turn with the view; the needle points
        to the north (-z). Click it to put the north up."""
        c, R = self._compass_geo()
        yaw = math.radians(self.yaw)

        def screen(wx, wz, r):              # world direction → point at radius r
            sx = wx * math.cos(yaw) - wz * math.sin(yaw)
            up = -wx * math.sin(yaw) - wz * math.cos(yaw)
            n = math.hypot(sx, up) or 1.0
            return QPointF(c.x() + sx / n * r, c.y() - up / n * r)
        hov = getattr(self, "_compass_hover", False)
        p.setPen(QPen(QColor(255, 255, 255, 70 if not hov else 140), 1.2))
        p.setBrush(QColor(12, 14, 18, 175 if not hov else 205))
        p.drawEllipse(c, R, R)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, R - 11, R - 11)
        for k in range(16):                 # ticks every 22.5°
            a = k * math.pi / 8
            wx, wz = math.sin(a), -math.cos(a)
            big = k % 4 == 0
            if big:
                continue
            p.setPen(QPen(QColor(255, 255, 255, 120 if k % 2 == 0 else 60), 1.2))
            p.drawLine(screen(wx, wz, R - 3), screen(wx, wz, R - (7 if k % 2 == 0 else 5)))
        # needle: a diamond, red half to the north
        tip_n, tip_s = screen(0, -1, R - 14), screen(0, 1, R - 14)
        left, right = screen(-1, 0, 4.6), screen(1, 0, 4.6)
        from PySide6.QtGui import QPolygonF
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(232, 72, 64))
        p.drawPolygon(QPolygonF([tip_n, left, c, right]))
        p.setBrush(QColor(226, 228, 234))
        p.drawPolygon(QPolygonF([tip_s, left, c, right]))
        p.setBrush(QColor(30, 32, 38))
        p.drawEllipse(c, 2.0, 2.0)
        # letters on the dial
        f = theme.ui_font(8.0, QFont.Weight.Bold)
        p.setFont(f)
        for txt, wx, wz, col in (("N", 0, -1, QColor(255, 108, 98)), ("E", 1, 0, QColor(225, 228, 235)),
                                 ("S", 0, 1, QColor(225, 228, 235)), ("W", -1, 0, QColor(225, 228, 235))):
            q = screen(wx, wz, R - 5.5)
            p.setPen(col)
            p.drawText(QRectF(q.x() - 7, q.y() - 7, 14, 14), Qt.AlignmentFlag.AlignCenter, txt)

    def _on_compass(self, pos) -> bool:
        c, R = self._compass_geo()
        return math.hypot(pos.x() - c.x(), pos.y() - c.y()) <= R

    # ── picking ──────────────────────────────────────────────────────────────
    def _ray(self, pos: QPoint):
        proj, view, eye = self._matrices()
        inv, ok = (proj * view).inverted()
        if not ok:
            return None
        x = 2.0 * pos.x() / max(1, self.width()) - 1
        y = 1 - 2.0 * pos.y() / max(1, self.height())
        a = inv.map(QVector4D(x, y, -1, 1))
        b = inv.map(QVector4D(x, y, 1, 1))
        a = QVector3D(a.x() / a.w(), a.y() / a.w(), a.z() / a.w())
        b = QVector3D(b.x() / b.w(), b.y() / b.w(), b.z() / b.w())
        d = (b - a).normalized()
        return a, d

    def pick(self, pos: QPoint):
        """('block', (x, y, z), info) | ('ground', (x, z)) | None"""
        r = self._ray(pos)
        if r is None:
            return None
        o, d = r
        ox, oy, oz = o.x(), o.y(), o.z()
        dx, dy, dz = d.x(), d.y(), d.z()
        x, y, z = math.floor(ox), math.floor(oy), math.floor(oz)
        sx, sy, sz = (1 if dx > 0 else -1), (1 if dy > 0 else -1), (1 if dz > 0 else -1)

        def tmax(o_, d_, c):
            if d_ == 0:
                return math.inf
            nb = c + (1 if d_ > 0 else 0)
            return (nb - o_) / d_
        tx, ty, tz = tmax(ox, dx, x), tmax(oy, dy, y), tmax(oz, dz, z)
        ddx = abs(1 / dx) if dx else math.inf
        ddy = abs(1 / dy) if dy else math.inf
        ddz = abs(1 / dz) if dz else math.inf
        for _ in range(1200):
            b = self._pick.get((x, y, z))
            if b is not None:
                return ("block", (x, y, z), b)
            if y < GROUND_Y:
                break
            if tx < ty and tx < tz:
                x += sx; tx += ddx
            elif ty < tz:
                y += sy; ty += ddy
            else:
                z += sz; tz += ddz
        if dy < 0:
            t = (GROUND_Y - oy) / dy
            gx, gz = ox + dx * t, oz + dz * t
            if (math.floor(gx) >> 4, math.floor(gz) >> 4) in getattr(self, "_ground", set()):
                return ("ground", (math.floor(gx), math.floor(gz)))
        return None

    # ── mouse / keys ─────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.MouseButton.LeftButton and self._on_compass(e.position()):
            self.face_north()
            self._press = None
            self._drag = None
            self._moved = True              # no click on the scene behind
            return
        self._press = e.position().toPoint()
        self._drag = (e.button(), self.yaw, self.pitch, QVector3D(self.target), e.modifiers())
        self._moved = False

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        on_c = self._drag is None and self._on_compass(e.position())
        if on_c != getattr(self, "_compass_hover", False):
            self._compass_hover = on_c
            self.setToolTip("Click: north up" if on_c else "")
            self.update()
        if on_c:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            return
        if self._drag is not None and self._press is not None:
            btn, yaw0, pitch0, tgt0, mods = self._drag
            dx, dy = pos.x() - self._press.x(), pos.y() - self._press.y()
            if abs(dx) + abs(dy) > 3:
                self._moved = True
            if self._moved:
                pan = btn in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton) or \
                    (mods & Qt.KeyboardModifier.ShiftModifier)
                if pan:
                    s = self.dist / max(200, self.height()) * 1.15
                    yaw = math.radians(self.yaw)
                    right = QVector3D(math.cos(yaw), 0, -math.sin(yaw))
                    fwd = QVector3D(-math.sin(yaw), 0, -math.cos(yaw))
                    self.target = tgt0 - right * (dx * s) + fwd * (dy * s)
                else:
                    self.yaw = yaw0 - dx * 0.4
                    self.pitch = max(8.0, min(89.0, pitch0 + dy * 0.3))
                self.update()
                return
        hit = self.pick(pos)
        hv = None
        txt = ""
        if hit and hit[0] == "block":
            x, y, z = hit[1]
            hv = (x, y, z)
            ti, li, ci = hit[2][4]
            if li >= 0:
                lname = ("lowest", "middle", "top")[li]
                cname = ("north-west", "south-west", "north-east", "south-east")[ci]
                st = {"corner_on": "leaf", "corner_off": "no leaf", "corner_unk": "unknown"}[hit[2][3]]
                txt = f"{x} {y} {z}  ·  {lname} layer, {cname} corner: {st}  (click to change)"
            else:
                txt = f"{x} {y} {z}  ·  tree {ti + 1}"
        elif hit and hit[0] == "ground":
            gx, gz = hit[1]
            hv = (gx, gz)
            txt = f"{gx} {GROUND_Y} {gz}  ·  chunk {gx >> 4} {gz >> 4}  ·  in chunk {gx & 15} {gz & 15}"
        if hv != self._hover:
            self._hover = hv
            self.update()
        self.hoverText.emit(txt)
        self.setCursor(Qt.CursorShape.PointingHandCursor if hit and hit[0] == "block"
                       else Qt.CursorShape.CrossCursor if self.tool == "add" else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, e):
        moved = self._moved
        self._drag = None
        if moved or e.button() != Qt.MouseButton.LeftButton:
            return
        hit = self.pick(e.position().toPoint())
        if hit is None:
            return
        if hit[0] == "block":
            ti, li, ci = hit[2][4]
            if li >= 0:
                self.cornerClicked.emit(ti, li, ci)
            else:
                self.treeSelected.emit(ti)
        elif self.tool == "add":
            self.groundClicked.emit(*hit[1])
        else:
            self.treeSelected.emit(-1)

    def mouseDoubleClickEvent(self, e):
        hit = self.pick(e.position().toPoint())
        if hit and hit[0] == "ground" and e.button() == Qt.MouseButton.LeftButton:
            self.groundClicked.emit(*hit[1])

    def wheelEvent(self, e):
        k = 0.88 if e.angleDelta().y() > 0 else 1 / 0.88
        self.dist = max(6.0, min(400.0, self.dist * k))
        self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.hoverText.emit("")
        self.update()

    def keyPressEvent(self, e):
        k = e.key()
        moves = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
                 Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if k in moves and self.selected >= 0:
            # arrows follow the view: "up" moves away from the camera
            dx, dz = moves[k]
            yaw = self.yaw % 360
            q = int(((yaw + 45) % 360) // 90)       # 0: looking towards -z
            for _ in range(q):
                dx, dz = dz, -dx
            self.moveSelected.emit(dx, dz)
            return
        if k in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self.selected >= 0:
            self.deleteSelected.emit()
            return
        if k == Qt.Key.Key_Escape:
            if self.tool == "add":
                self.set_tool("select")
                self.update()
            return
        super().keyPressEvent(e)


def _sky_color() -> QColor:
    c = QColor(theme.BG1 or "#22252c")
    if theme.IS_LIGHT:
        return QColor(198, 220, 240)
    return QColor(int(c.red() * 0.6 + 40 * 0.4), int(c.green() * 0.6 + 52 * 0.4), int(c.blue() * 0.6 + 70 * 0.4))


def _voidp(off):
    from shiboken6 import VoidPtr
    return VoidPtr(off)
