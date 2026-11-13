"""
tree_crack.py — (05/11) Seedcracking from trees: the trees seen in a world (position,
type, height, corner leaves) → the structure seed (48 bits) → the world seeds.

Java 1.14 – 1.17.1, Forest and Birch Forest (oak, birch, fancy oak), Taiga (1.16+).
(06/11) Also, any version up to 26.x: structures (struct_crack.py) and the End pillars
(end_pillars.py) — from 1.18 on, the only way (the trees' random uses the 64-bit seed).

    scene = Scene(version="1.16.5", trees=[Tree(299, 12, "oak", 6, "011100110110"), ...])
    res = crack(scene, on_progress=..., stop=threading.Event())   # structure seeds
    world_seeds(ss, scene)                                          # their world seeds

The engine (cubiomes_src/treecrack_core.h) runs on the GPU (tree_cuda.dll, built by
tools/build_native.py) or on every CPU thread (treecrack.dll, g++). Method of Andrew
(Gaider10)'s TreeCracker + PopulationCrr, reimplemented for SSA (credits:
THIRD_PARTY_LICENSES.txt). Tree models checked on real 1.16.1 / 1.16.5 / 1.17.1 worlds.

Leaves: 12 characters, '1' leaf, '0' no leaf, '?' unknown — the 3 random layers from
the lowest (two below the top log) to the top log's layer, each NW, SW, NE, SE.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import platform
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

M48 = (1 << 48) - 1
MULT = 0x5DEECE66D

# label, engine id, decoration salt, cubiomes version id
# (06/11) 1.14 / 1.15: forests, checked against Gaider10 / NelS' test data (known seeds);
# 1.16+: every biome checked on real servers
VERSIONS = {
    "1.14.4": ("Java 1.14 – 1.14.4", 4, 60001, 17),
    "1.15.2": ("Java 1.15 – 1.15.2", 5, 60001, 18),
    "1.16.1": ("Java 1.16 – 1.16.1", 1, 80001, 19),
    "1.16.5": ("Java 1.16.2 – 1.16.5", 2, 80001, 20),
    "1.17.1": ("Java 1.17 – 1.17.1", 3, 80002, 21),
    # (06/11) 1.18+: structures / End pillars only — the trees' random comes from the
    # whole 64-bit world seed (Xoroshiro population seed): they can't give the 48 bits
    "1.18.2": ("Java 1.18 – 1.18.2", None, None, 22),
    "1.19.2": ("Java 1.19 – 1.19.2", None, None, 23),
    "1.19.4": ("Java 1.19.3 – 1.19.4", None, None, 24),
    "1.20.6": ("Java 1.20 – 1.20.6", None, None, 25),
    "1.21.1": ("Java 1.21 – 1.21.1", None, None, 26),
    "1.21.3": ("Java 1.21.2 – 1.21.3", None, None, 27),
    "1.21.8": ("Java 1.21.4 – 1.21.8", None, None, 28),
    "1.21.11": ("Java 1.21.9 – 1.21.11", None, None, 32),
    "26.1": ("Java 26.1", None, None, 33),
    "26.2": ("Java 26.2", None, None, 34),
    "26.3": ("Java 26.3", None, None, 35),
}
TREE_VERSIONS = tuple(k for k, v in VERSIONS.items() if v[1] is not None)
# key: (engine id, label, cubiomes biome ids with these trees: Forest / Wooded Hills,
#       Birch Forest / Birch Forest Hills, Taiga / Taiga Hills / Snowy Taiga (+ Hills))
BIOMES = {"forest": (0, "Forest", (4, 18)), "birch_forest": (1, "Birch Forest", (27, 155)),
          "taiga": (2, "Taiga", (5, 19, 30, 31))}
BIOME_TYPES = {"forest": ("oak", "birch", "fancy"), "birch_forest": ("birch",),
               "taiga": ("spruce", "pine")}
TAIGA_VERSIONS = ("1.16.1", "1.16.5", "1.17.1")
TYPES = ("oak", "birch", "fancy", "spruce", "pine", "unknown")
TYPE_BIT = {"oak": 1, "birch": 2, "fancy": 4, "spruce": 8, "pine": 16, "unknown": 0}
TYPE_INDEX = {"oak": 0, "birch": 1, "fancy": 2, "spruce": 3, "pine": 4}
HEIGHTS = {"oak": (4, 6), "birch": (5, 7), "fancy": (4, 12), "spruce": (5, 8), "pine": (6, 10)}
# conifer details: key, label, range
SPRUCE_ATTRS = (("bare", "Bare trunk", (1, 2)), ("above", "Layers above the trunk", (1, 3)),
                ("widest", "Widest radius", (2, 3)), ("top", "Top radius", (0, 1)))
PINE_ATTRS = (("layers", "Leaf layers", (3, 5)), ("widest", "Widest radius", (1, 3)))
CORNERS = ("NW", "SW", "NE", "SE")
LAYERS = ("Lowest", "Middle", "Top")

# fancy oak: the straight column of logs one sees from the ground, per generator
# height (measured on 1.16.1 / 1.16.5 / 1.17.1 worlds)
FANCY_VISIBLE = {3: 4, 4: 4, 5: 5, 6: 5, 7: 6, 8: 7, 9: 7, 10: 8, 11: 9, 12: 10, 13: 11, 14: 12}


def fancy_heights(visible: int) -> list:
    tol = 1 if visible >= 10 else 0
    return [h for h, v in FANCY_VISIBLE.items() if abs(v - visible) <= tol]


# ── scene ──────────────────────────────────────────────────────────────────────
@dataclass
class Tree:
    x: int
    z: int
    type: str = "oak"                 # oak | birch | fancy | spruce | pine | unknown
    height: int | None = None         # logs of the trunk (None = unknown)
    leaves: str = "?" * 12
    y: int = 64                       # ground level (display only)
    attrs: dict = field(default_factory=dict)   # spruce / pine details (SPRUCE_ATTRS…)

    def to_json(self):
        d = {"x": self.x, "z": self.z, "type": self.type, "height": self.height,
             "leaves": self.leaves, "y": self.y}
        if self.attrs:
            d["attrs"] = dict(self.attrs)
        return d

    @staticmethod
    def from_json(d):
        return Tree(int(d["x"]), int(d["z"]), d.get("type", "oak"), d.get("height"),
                    (d.get("leaves") or "?" * 12)[:12].ljust(12, "?"), int(d.get("y", 64)),
                    {k: int(v) for k, v in (d.get("attrs") or {}).items() if v is not None})

    @property
    def chunk(self):
        return self.x >> 4, self.z >> 4

    def leaf(self, layer: int, corner: int) -> str:
        return self.leaves[layer * 4 + corner]

    def set_leaf(self, layer: int, corner: int, v: str):
        i = layer * 4 + corner
        self.leaves = self.leaves[:i] + v + self.leaves[i + 1:]

    def has_leaves(self) -> bool:
        return self.type in ("oak", "birch")


@dataclass
class Scene:
    version: str = "1.16.5"
    trees: list = field(default_factory=list)
    biomes: dict = field(default_factory=dict)      # (cx, cz) -> forest | birch_forest
    name: str = ""
    # (06/11) the End's obsidian pillars seen (end_pillars.py): {"sizes": [10 × 0..9 | None],
    # "cages": [10 × bool | None], "rot": orientation unknown}
    pillars: dict = field(default_factory=dict)
    # (06/11) structures seen: [struct_crack.Obs]
    structures: list = field(default_factory=list)

    def biome(self, ch) -> str:
        return self.biomes.get(tuple(ch), "forest")

    def chunks(self) -> dict:
        out = {}
        for t in self.trees:
            out.setdefault(t.chunk, []).append(t)
        return out

    def to_json(self):
        return {"format": "ssa-trees", "v": 1, "version": self.version, "name": self.name,
                "trees": [t.to_json() for t in self.trees],
                "biomes": [[cx, cz, b] for (cx, cz), b in self.biomes.items()],
                **({"pillars": self.pillars} if pillars_used(self) else {}),
                **({"structures": [o.to_json() for o in self.structures]} if self.structures else {})}

    @staticmethod
    def from_json(d):
        s = Scene(d.get("version", "1.16.5"), [Tree.from_json(t) for t in d.get("trees", [])],
                  {(int(a), int(b)): c for a, b, c in d.get("biomes", [])}, d.get("name", ""),
                  dict(d.get("pillars") or {}))
        try:
            import struct_crack as stc
            s.structures = [stc.Obs.from_json(o) for o in d.get("structures") or []
                            if o.get("key") in stc.STRUCTS]
        except Exception:
            s.structures = []
        if s.version not in VERSIONS:
            s.version = "1.16.5"
        return s

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, indent=1)

    @staticmethod
    def load(path):
        with open(path, encoding="utf-8") as f:
            return Scene.from_json(json.load(f))


# ── information ────────────────────────────────────────────────────────────────
def _type_prob(t: str, biome: str) -> float:
    if t == "unknown":
        return 1.0
    if biome == "birch_forest":
        return 1.0 if t == "birch" else 0.0
    if biome == "taiga":
        return {"spruce": 2 / 3, "pine": 1 / 3}.get(t, 0.0)
    return {"birch": 0.2, "fancy": 0.08, "oak": 0.72}.get(t, 0.0)


def _conifer_dist(typ: str) -> list:
    """every equally likely draw of a spruce / pine (1.16+): [(trunk, attrs dict)]"""
    out = []
    if typ == "spruce":
        for a in range(3):
            for b in range(2):
                t = 5 + a + b
                for ft in (1, 2):
                    fh = max(4, t - ft)
                    for rad in (2, 3):
                        for off in range(3):
                            for top in range(2):
                                out.append((t, {"bare": t - fh, "above": off + 1, "top": top,
                                                "widest": rad if fh + off + 1 + top >= 8 else 2}, 1.0))
    else:
        for a in range(5):
            t = 6 + a
            for fh in (3, 4):
                n = max(t - fh + 1, 1)
                for r in range(n):
                    fr = 1 + r
                    out.append((t, {"layers": fh + 1 - (fr == 1), "widest": min(fr, fh - 1)}, 1.0 / n))
    return out


def _conifer_bits(t) -> float:
    dist = _conifer_dist(t.type)
    tot = sum(w for _t, _a, w in dist)
    ok = sum(w for tr, at, w in dist
             if (t.height is None or tr == t.height)
             and all(at.get(k) == v for k, v in (t.attrs or {}).items() if v is not None))
    return math.log2(tot / ok) if ok > 0 else 0.0


def height_options(t: Tree) -> list:
    """the generator heights the entered height allows"""
    if t.type == "fancy":
        return list(range(3, 15)) if t.height is None else fancy_heights(int(t.height))
    lo, hi = HEIGHTS.get(t.type, (4, 7))
    if t.height is None:
        return list(range(lo, hi + 1))
    return [t.height] if lo <= t.height <= hi else []


CANOPY = {"oak": 2, "birch": 2, "fancy": 4, "spruce": 3, "pine": 3, "unknown": 2}


def risky_corners(scene: Scene, t: Tree) -> set:
    """(layer, corner) of t's corners seen WITH a leaf that another entered tree's
    leaves can reach: that leaf may be the other tree's, not this one's."""
    if not t.has_leaves():
        return set()
    out = set()
    others = [o for o in scene.trees if o is not t and abs(o.x - t.x) <= 7 and abs(o.z - t.z) <= 7]
    for li in range(3):
        r = 1 if li == 2 else 2
        for ci, (dx, dz) in enumerate(((-r, -r), (-r, r), (r, -r), (r, r))):
            if t.leaves[li * 4 + ci] != "1":
                continue
            X, Z = t.x + dx, t.z + dz
            if any(max(abs(o.x - X), abs(o.z - Z)) <= CANOPY.get(o.type, 2) for o in others):
                out.add((li, ci))
    return out


def safe_scene(scene: Scene) -> Scene:
    """the scene with the risky corners (risky_corners) counted as unknown"""
    sc = Scene.from_json(scene.to_json())
    for t, t0 in zip(sc.trees, scene.trees):
        for li, ci in risky_corners(scene, t0):
            t.set_leaf(li, ci, "?")
    return sc


def tree_bits(t: Tree, biome: str = "forest") -> float:
    """bits of information one tree gives: position (8), type, height, leaves"""
    b = 8.0
    p = _type_prob(t.type, biome)
    if 0 < p < 1:
        b += -math.log2(p)
    if t.type in ("spruce", "pine"):
        b += _conifer_bits(t)
    elif t.type in ("oak", "birch") and t.height is not None:
        b += math.log2(3)
    elif t.type == "fancy" and t.height is not None:
        b += math.log2(12 / max(1, len(fancy_heights(int(t.height)))))
    if t.has_leaves():
        b += sum(1 for c in t.leaves if c in "01")
    return b


def chunk_bits(trees, biome="forest") -> float:
    return sum(tree_bits(t, biome) for t in trees)


# bits a chunk needs: 48 (structure seed) + about 8 for where the trees start in the
# random sequence and which ones grew → below that, false seeds come out
BITS_NEEDED = 56.0
BITS_MINIMUM = 48.0


def expected_false(bits: float) -> float:
    return 2.0 ** (BITS_NEEDED - bits)


def validate(scene: Scene) -> list:
    """problems that would make the crack fail or pointless: [(level, text)]"""
    out = []
    if scene.version not in VERSIONS:
        out.append(("error", "Unsupported version"))
    elif scene.trees and scene.version not in TREE_VERSIONS:
        out.append(("error", "Trees can't crack Java 1.18+ (their random depends on the whole "
                             "64-bit world seed): use structures and the End pillars"))
    if scene.structures and scene.version in VERSIONS:
        import struct_crack as stc
        out += stc.problems(scene.structures, VERSIONS[scene.version][3])
    seen = {}
    for t in scene.trees:
        k = (t.x, t.z)
        if k in seen:
            out.append(("error", f"Two trees at {t.x}, {t.z}"))
        seen[k] = t
        bio = scene.biome(t.chunk)
        if t.type != "unknown" and t.type not in BIOME_TYPES[bio]:
            out.append(("error", f"{TYPE_NAME[t.type].capitalize()} at {t.x}, {t.z}: a "
                                 f"{BIOMES[bio][1]} doesn't grow it"))
        if bio == "taiga" and scene.version not in TAIGA_VERSIONS:
            out.append(("error", f"Chunk {t.chunk[0]}, {t.chunk[1]}: taiga trees need Java 1.16 or newer "
                                 "(not available for this version yet)"))
        if t.type in ("oak", "birch", "spruce", "pine") and t.height is not None:
            lo, hi = HEIGHTS[t.type]
            if not lo <= t.height <= hi:
                out.append(("error", f"{t.type.capitalize()} at {t.x}, {t.z}: height {t.height} "
                                     f"(a {t.type} trunk is {lo} to {hi} logs)"))
    for ch, trees in scene.chunks().items():
        if len(trees) > 11:
            out.append(("error", f"Chunk {ch[0]}, {ch[1]}: {len(trees)} trees (a chunk grows 11 at most)"))
    return out


# ── native libraries ──────────────────────────────────────────────────────────
class _TcTree(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("z", ctypes.c_int32), ("types", ctypes.c_int32),
                ("hmin", ctypes.c_int32 * 5), ("hmax", ctypes.c_int32 * 5),
                ("leaf_known", ctypes.c_int32), ("leaf_val", ctypes.c_int32),
                ("amin", ctypes.c_int32 * 4), ("amax", ctypes.c_int32 * 4),
                ("pmin", ctypes.c_int32 * 2), ("pmax", ctypes.c_int32 * 2)]


class _TcChunk(ctypes.Structure):
    _fields_ = [("version", ctypes.c_int32), ("biome", ctypes.c_int32), ("cx", ctypes.c_int32),
                ("cz", ctypes.c_int32), ("salt", ctypes.c_int32), ("ntrees", ctypes.c_int32),
                ("t", _TcTree * 16)]


_U64, _I64 = ctypes.c_uint64, ctypes.c_int64
_PC = ctypes.POINTER(_TcChunk)
_LIB = None
_LIB_ERR = None
_SRC = ("cubiomes_src/treecrack.cpp", "cubiomes_src/treecrack_core.h", "cubiomes_src/structcrack_core.h")


def _here():
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


def _lib_name():
    return "treecrack.dll" if platform.system() == "Windows" else "libtreecrack.so"


def _stale(out):
    if getattr(sys, "frozen", False):
        return False
    try:
        t = os.path.getmtime(out)
        return any(os.path.getmtime(os.path.join(_here(), s)) > t for s in _SRC)
    except OSError:
        return True


def _compile():
    global _LIB_ERR
    here = _here()
    out = os.path.join(here, _lib_name())
    if getattr(sys, "frozen", False):
        _LIB_ERR = f"{_lib_name()} is missing (reinstall SimpleSeedAnalyser)"
        return None
    tmp = out + ".building"
    cmd = ["g++", "-std=c++17", "-O3", "-shared", "-fPIC", f"-I{os.path.join(here, 'cubiomes_src')}",
           "-o", tmp, os.path.join(here, "cubiomes_src", "treecrack.cpp")]
    if platform.system() == "Windows":
        cmd += ["-static", "-static-libgcc", "-static-libstdc++"]
    else:
        cmd += ["-lpthread"]
    print("Compiling the tree cracker (one time, ~5 s)…", flush=True)
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=180)
    except FileNotFoundError:
        _LIB_ERR = "g++ not found (install MinGW / w64devkit)"
        return None
    except subprocess.TimeoutExpired:
        _LIB_ERR = "g++ timed out"
        return None
    if r.returncode != 0:
        _LIB_ERR = "g++ failed: " + (r.stderr or b"").decode(errors="replace")[:800]
        return None
    try:
        os.replace(tmp, out)
    except OSError:                    # loaded by this process: keep the old one
        return tmp
    return out


def _bind(lib):
    P8 = ctypes.POINTER(ctypes.c_uint8)
    PU = ctypes.POINTER(_U64)
    for name, res, args in (
            ("tc_version", ctypes.c_int, []),
            ("tc_lattice_size", None, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(_I64), ctypes.POINTER(_I64)]),
            ("tc_cpu_scan", _I64, [_PC, _I64, _I64, PU, _I64, ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p]),
            ("tc_exact", _I64, [_PC, PU, _I64, PU, _I64, ctypes.c_int]),
            ("tc_reverse", ctypes.c_int, [_PC, _U64, PU, ctypes.c_int]),
            ("tc_check", None, [_PC, PU, _I64, P8, ctypes.c_int]),
            ("tc_cost_ss", None, [_PC, PU, _I64, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_int32)]),
            ("tc_trees", ctypes.c_int, [_U64, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                        ctypes.c_int, ctypes.POINTER(ctypes.c_int)]),
            ("tc_trees_mask", ctypes.c_int, [_U64, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                             ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(ctypes.c_int)])):
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = args


def lib():
    """the CPU library (compiled on first use on a development machine)"""
    global _LIB, _LIB_ERR
    if _LIB is not None:
        return _LIB
    p = os.path.join(_here(), _lib_name())
    if not os.path.exists(p) or _stale(p):
        q = _compile()
        if q is None and not os.path.exists(p):
            raise RuntimeError(_LIB_ERR or "tree cracker library missing")
        p = q or p
    try:
        L = ctypes.CDLL(p)
        _bind(L)
    except Exception as e:
        _LIB_ERR = f"cannot load {os.path.basename(p)}: {e}"
        raise RuntimeError(_LIB_ERR)
    _LIB = L
    return L


_GPU = None          # (lib | None, message)


def gpu():
    """(lib, message) of tree_cuda — lib is None when there is no usable GPU"""
    global _GPU
    if _GPU is not None:
        return _GPU
    try:
        import native_libs as nl
        g, msg = nl.load_cuda("tree_cuda")
    except Exception as e:
        g, msg = None, str(e)
    if g is not None:
        try:
            g.tree_cuda_scan.restype = _I64
            g.tree_cuda_scan.argtypes = [_PC, _I64, _I64, ctypes.POINTER(_U64), _I64]
            if not _gpu_selftest(g):
                g, msg = None, "GPU tree cracker self-test failed — using the CPU"
        except AttributeError:
            g, msg = None, "tree_cuda library too old (rebuild: py tools/build_native.py)"
    _GPU = (g, msg)
    return _GPU


# a known chunk (seed 12345, Java 1.16.5, chunk 18, 0) and where its first tree lies
_SELFTEST = ("1.16.5", [(299, 12, "oak", 6, "011100110110"), (301, 9, "oak", 6, "0?0111001111"),
                        (294, 14, "oak", 5, "1?1001001000"), (301, 3, "birch", 7, "0?1000010110"),
                        (297, 4, "birch", 6, "111101?01100")], 712727, 31410661108861)


def _gpu_selftest(g) -> bool:
    ver, trees, row, s0 = _SELFTEST
    sc = Scene(ver, [Tree(x, z, ty, h, lv) for x, z, ty, h, lv in trees])
    c = _chunk_struct(sc, (18, 0), order=sc.trees)
    out = (_U64 * 64)()
    n = g.tree_cuda_scan(ctypes.byref(c), row - 2, 5, out, 64)
    return n >= 1 and s0 in list(out[:min(n, 64)])


# ── engine ─────────────────────────────────────────────────────────────────────
def _chunk_struct(scene: Scene, ch, order=None) -> _TcChunk:
    _lab, vid, salt, _mc = VERSIONS[scene.version]
    bio = scene.biome(ch)
    trees = order if order is not None else sorted(
        scene.chunks().get(tuple(ch), []), key=lambda t: tree_bits(t, bio), reverse=True)
    c = _TcChunk()
    c.version, c.biome, c.salt = vid, BIOMES[bio][0], salt
    c.cx, c.cz = ch
    c.ntrees = min(len(trees), 16)
    for i, t in enumerate(trees[:16]):
        s = c.t[i]
        s.x, s.z = t.x & 15, t.z & 15
        s.types = TYPE_BIT.get(t.type, 0)
        for k in range(5):
            s.hmin[k] = s.hmax[k] = -1
        for k in range(4):
            s.amin[k] = s.amax[k] = -1
        for k in range(2):
            s.pmin[k] = s.pmax[k] = -1
        if t.height is not None and t.type in TYPE_INDEX and not (
                t.type == "fancy" and vid in (4, 5)):          # 1.14 / 1.15: fancy height unused
            hs = height_options(t)
            if hs:
                k = TYPE_INDEX[t.type]
                s.hmin[k], s.hmax[k] = min(hs), max(hs)
        if t.type == "spruce":
            for k, (key, _l, _r) in enumerate(SPRUCE_ATTRS):
                v = (t.attrs or {}).get(key)
                if v is not None:
                    s.amin[k] = s.amax[k] = int(v)
        elif t.type == "pine":
            for k, (key, _l, _r) in enumerate(PINE_ATTRS):
                v = (t.attrs or {}).get(key)
                if v is not None:
                    s.pmin[k] = s.pmax[k] = int(v)
        kn = val = 0
        if t.has_leaves():
            for b, chr_ in enumerate(t.leaves[:12]):
                if chr_ in "01":
                    kn |= 1 << b
                    val |= (chr_ == "1") << b
        s.leaf_known, s.leaf_val = kn, val
    return c


# ── (06/11) End pillars: 16 more bits (of the 32 low bits of the structure seed) ──
_PILLAR_CACHE = {}


def pillars_used(scene: Scene) -> bool:
    p = scene.pillars or {}
    return any(v is not None for v in (p.get("sizes") or [])) or \
        any(v is not None for v in (p.get("cages") or []))


def pillar_set(scene: Scene):
    """the pillar seeds (0..65535) that fit the pillars entered — None when none is"""
    if not pillars_used(scene):
        return None
    import end_pillars as ep
    p = scene.pillars
    key = (tuple(p.get("sizes") or ()), tuple(p.get("cages") or ()), bool(p.get("rot")))
    if key not in _PILLAR_CACHE:
        _PILLAR_CACHE.clear()
        _PILLAR_CACHE[key] = frozenset(ep.matching(p.get("sizes") or [], p.get("cages"),
                                                   rotation_unknown=bool(p.get("rot"))))
    return _PILLAR_CACHE[key]


def pillar_bits(scene: Scene) -> float:
    ps = pillar_set(scene)
    return 0.0 if ps is None else math.log2(65536 / max(1, len(ps)))


def pillar_filter(scene: Scene, seeds) -> list:
    ps = pillar_set(scene)
    if ps is None:
        return list(seeds)
    import end_pillars as ep
    return [s for s in seeds if ep.pillar_seed(s) in ps]


def finish(res: "CrackResult", scene: Scene) -> "CrackResult":
    """(re)apply the pillars to res.all_seeds, then rank (costs, random seeds)"""
    seeds = pillar_filter(scene, res.all_seeds)
    res.pillar_removed = len(res.all_seeds) - len(seeds)
    if scene.structures:
        import struct_crack as stc
        n0 = len(seeds)
        seeds = stc.check(scene.structures, VERSIONS[scene.version][3], seeds)
        res.struct_removed = n0 - len(seeds)
    res.costs = unexplained(scene, seeds) if len(seeds) <= 20000 and scene.trees and \
        scene.version in TREE_VERSIONS else {}
    # (06/11) a world made with a blank seed field: its structure seed has a world seed
    # nextLong() can give, with the entered biomes — rare for a false one (~5 %)
    try:
        res.randoms = {s: r for s in seeds if (r := random_matches(s, scene))} if len(seeds) <= 4096 else {}
    except Exception:
        res.randoms = {}
    res.structure_seeds = sorted(seeds, key=lambda s: (0 if s in res.randoms else 1,
                                                       res.costs.get(s, 99), s))
    return res


def best_chunk(scene: Scene):
    best = None
    for ch, trees in scene.chunks().items():
        b = chunk_bits(trees, scene.biome(ch))
        if best is None or b > best[1]:
            best = (ch, b)
    return best


@dataclass
class CrackResult:
    structure_seeds: list = field(default_factory=list)
    main_chunk: tuple = None
    checked: dict = field(default_factory=dict)      # chunk -> seeds left after it
    costs: dict = field(default_factory=dict)        # seed -> unexplained trees (0 = best)
    randoms: dict = field(default_factory=dict)      # seed -> random world seeds with the biomes
    device: str = ""
    seconds: float = 0.0
    stopped: bool = False
    candidates: int = 0
    all_seeds: list = field(default_factory=list)    # before the End pillars
    pillar_removed: int = 0
    struct_removed: int = 0
    tree_removed: int = 0
    mode: str = "trees"                               # trees | structures


def crack(scene: Scene, device: str = "auto", threads: int = 0, on_progress=None,
          stop: threading.Event | None = None, on_seed=None, row_range=None,
          shared_unknown: bool = True) -> CrackResult:
    """The structure seeds that grow every entered tree. on_progress(frac, text);
    on_seed(ss) as soon as one is found (before the other chunks' check).
    shared_unknown: corners seen with a leaf that another tree may have placed
    count as unknown (risky_corners) — a wrong corner loses the seed."""
    if shared_unknown:
        scene = safe_scene(scene)
    problems = [p for p in validate(scene) if p[0] == "error"]
    if problems:
        raise ValueError(problems[0][1])
    threads = threads or max(1, (os.cpu_count() or 2))
    mode = crack_mode(scene, device, threads)
    if mode is None:
        raise ValueError("Not enough information: add trees, or structures")
    if mode == "structures":
        return _crack_structures(scene, threads, on_progress, stop, device)
    bc = best_chunk(scene)
    if bc is None:
        raise ValueError("No trees")
    ch, _bits = bc
    L = lib()
    threads = threads or max(1, (os.cpu_count() or 2))
    c = _chunk_struct(scene, ch)
    ci, cj = _I64(), _I64()
    L.tc_lattice_size(c.t[0].x, c.t[0].z, ctypes.byref(ci), ctypes.byref(cj))
    rows = cj.value
    g = None
    if device in ("auto", "gpu"):
        g, msg = gpu()
        if g is None and device == "gpu":
            raise RuntimeError(msg)
    res = CrackResult(main_chunk=ch, device="GPU" if g is not None else f"CPU · {threads} threads")
    t0 = time.time()
    cap = 1 << 16
    buf = (_U64 * cap)()
    deco_seen, ss_seen = set(), []
    stop_flag = ctypes.c_int(0)
    prog = ctypes.c_int64(0)

    def handle(n):
        n = min(n, cap)
        if n <= 0:
            return
        res.candidates += n
        out = (_U64 * (n * 4 + 64))()
        m = L.tc_exact(ctypes.byref(c), buf, n, out, n * 4 + 64, threads)
        sso = (_U64 * 64)()
        for d in list(out[:min(m, n * 4 + 64)]):
            if d in deco_seen:
                continue
            deco_seen.add(d)
            k = L.tc_reverse(ctypes.byref(c), d, sso, 64)
            for s in list(sso[:min(k, 64)]):
                if s not in ss_seen:
                    ss_seen.append(s)
                    if on_seed:
                        on_seed(s)

    batch = max(1, rows // (400 if g is not None else 2000))
    r = 0
    if row_range:                       # tests: a slice of the lattice
        r, rows = max(0, row_range[0]), min(rows, row_range[1])
        batch = max(1, min(batch, (rows - r) // 4 or 1))
    r_first = r
    while r < rows:
        if stop is not None and stop.is_set():
            res.stopped = True
            break
        nr = min(batch, rows - r)
        if g is not None:
            n = g.tree_cuda_scan(ctypes.byref(c), r, nr, buf, cap)
            if n < 0:
                import native_libs as nl
                raise RuntimeError("GPU error: " + nl.last_error(g))
        else:
            n = L.tc_cpu_scan(ctypes.byref(c), r, nr, buf, cap, threads,
                              ctypes.addressof(stop_flag), ctypes.addressof(prog))
        if n > cap:
            raise RuntimeError("Too many candidates: give the first tree more leaves / a height")
        handle(n)
        r += nr
        if on_progress:
            el = time.time() - t0
            frac = (r - r_first) / max(1, rows - r_first)
            eta = el / frac - el if frac else 0
            on_progress(frac, f"{res.device} · {_fmt_t(el)} · ~{_fmt_t(eta)} left · "
                                  f"{len(ss_seen)} seed{'s' * (len(ss_seen) != 1)}")
    # the other chunks
    seeds = list(ss_seen)
    for och in scene.chunks():
        if tuple(och) == tuple(ch) or not seeds:
            continue
        oc = _chunk_struct(scene, och)
        arr = (_U64 * len(seeds))(*seeds)
        ok = (ctypes.c_uint8 * len(seeds))()
        L.tc_check(ctypes.byref(oc), arr, len(seeds), ok, threads)
        seeds = [s for s, k in zip(seeds, ok) if k]
        res.checked[tuple(och)] = len(seeds)
    res.all_seeds = seeds
    finish(res, scene)
    res.seconds = time.time() - t0
    return res


def _cover(scene: Scene, ch) -> "ctypes.Array":
    """256 bits: the spots of chunk ch under an entered tree's canopy (or trunk)"""
    bits = (ctypes.c_uint32 * 8)()
    cx, cz = ch
    for t in scene.trees:
        r = 3 if t.type == "fancy" else 2
        for dx in range(-r, r + 1):
            for dz in range(-r, r + 1):
                x, z = t.x + dx - cx * 16, t.z + dz - cz * 16
                if 0 <= x < 16 and 0 <= z < 16:
                    i = x + 16 * z
                    bits[i >> 5] |= 1 << (i & 31)
    return bits


def unexplained(scene: Scene, seeds) -> dict:
    """seed -> the trees its replays leave unexplained, summed over the chunks: trees
    the game would have grown where nothing was entered, or that would not have
    grown in the open. The true seed usually scores lowest (0 when every tree of
    the entered chunks was entered)."""
    seeds = [s & M48 for s in seeds]
    if not seeds:
        return {}
    L = lib()
    tot = [0] * len(seeds)
    for ch in scene.chunks():
        oc = _chunk_struct(scene, ch)
        arr = (_U64 * len(seeds))(*seeds)
        cost = (ctypes.c_int32 * len(seeds))()
        L.tc_cost_ss(ctypes.byref(oc), arr, len(seeds), _cover(scene, ch), cost)
        for i, c in enumerate(cost):
            tot[i] += min(int(c), 99)
    return dict(zip(seeds, tot))


def check(scene: Scene, seeds, chunks=None) -> list:
    """the structure seeds of `seeds` that grow every entered tree of `chunks`"""
    L = lib()
    seeds = [s & M48 for s in seeds]
    for ch in (chunks or scene.chunks()):
        if not seeds:
            break
        oc = _chunk_struct(scene, ch)
        arr = (_U64 * len(seeds))(*seeds)
        ok = (ctypes.c_uint8 * len(seeds))()
        L.tc_check(ctypes.byref(oc), arr, len(seeds), ok, os.cpu_count() or 2)
        seeds = [s for s, k in zip(seeds, ok) if k]
    return seeds


def _decode(ch, x, z, ty, h, lv) -> "Tree":
    t = {1: "oak", 2: "birch", 4: "fancy", 8: "spruce", 16: "pine"}[ty]
    attrs = {}
    leaves = "?" * 12
    if t in ("oak", "birch"):
        leaves = "".join(str(lv >> b & 1) for b in range(12))
    elif t == "spruce":
        attrs = {"bare": lv & 15, "above": lv >> 4 & 15, "widest": lv >> 8 & 15, "top": lv >> 12 & 15}
    elif t == "pine":
        attrs = {"layers": lv & 15, "widest": lv >> 8 & 15}
    if t == "fancy":
        h = FANCY_VISIBLE.get(h) if h is not None and h >= 0 else None
    return Tree(ch[0] * 16 + x, ch[1] * 16 + z, t, h, leaves, 64, attrs)


def predicted_trees(ss: int, scene: Scene, ch) -> list:
    """the trees structure seed ss grows in chunk ch (each one, as if it grew):
    [Tree] in the order the game places them"""
    _lab, vid, salt, _mc = VERSIONS[scene.version]
    out = (ctypes.c_int * 6 * 12)()
    n = lib().tc_trees(ss & M48, vid, BIOMES[scene.biome(ch)][0], ch[0], ch[1], salt,
                       ctypes.cast(out, ctypes.POINTER(ctypes.c_int)))
    trees = []
    for i in range(n):
        x, z, ty, h, lv, _ = list(out[i])
        trees.append(_decode(ch, x, z, ty, h, lv))
    return trees


# ── test against a known seed ─────────────────────────────────────────────────
def real_trees(ss: int, scene: Scene, ch, grown: int) -> list:
    """the trees of chunk ch for structure seed ss, tree i grown iff bit i of grown:
    [(Tree, grown)]"""
    _lab, vid, salt, _mc = VERSIONS[scene.version]
    out = (ctypes.c_int * 6 * 12)()
    n = lib().tc_trees_mask(ss & M48, vid, BIOMES[scene.biome(ch)][0], ch[0], ch[1], salt, grown,
                            ctypes.cast(out, ctypes.POINTER(ctypes.c_int)))
    res = []
    for i in range(n):
        x, z, ty, h, lv, g = list(out[i])
        t = _decode(ch, x, z, ty, h, lv)
        if t.type == "fancy":
            t.height = h                   # the generator's height (diagnose compares it)
        res.append((t, bool(g)))
    return res


def diagnose(scene: Scene, world_seed: int) -> dict:
    """What the entered trees get wrong for a KNOWN seed (practice on a test world).
    For each chunk: the replay (which trees grew) that matches the most entered
    trees, then per entered tree the differences. Returns
    {"chunks": {ch: [real Tree that grew]}, "trees": [(index, [problem text])], "ok": bool}"""
    ss = int(world_seed) & M48
    out = {"chunks": {}, "trees": [], "ok": True}
    probs = {}
    for ch, trees in scene.chunks().items():
        best = None
        for mask in range(1 << 11):
            real = real_trees(ss, scene, ch, mask)
            if mask >> len(real):
                continue
            grown = {}
            for t, g in real:
                if g and (t.x, t.z) not in grown:
                    grown[(t.x, t.z)] = t
            score = 0
            for t in trees:
                r = grown.get((t.x, t.z))
                if r is None:
                    continue
                score += 100 if t.type in (r.type, "unknown") else 40
                if t.type == r.type and t.height is not None and t.has_leaves():
                    score += 3 * (t.height == r.height)
                    score += sum(1 for a, b in zip(t.leaves, r.leaves) if a in "01" and a == b)
            # fewer trees that grew where nothing was entered
            score -= sum(1 for k in grown if k not in {(t.x, t.z) for t in trees}) * 0.1
            if best is None or score > best[0]:
                best = (score, grown)
        grown = best[1]
        out["chunks"][ch] = list(grown.values())
        for t in trees:
            i = scene.trees.index(t)
            p = []
            r = grown.get((t.x, t.z))
            if r is None:
                near = sorted((max(abs(o.x - t.x), abs(o.z - t.z)), o) for o in grown.values()
                              if (o.x, o.z) not in {(u.x, u.z) for u in trees})
                hint = (f" — the nearest tree this seed grows that you didn't enter: {TYPE_NAME[near[0][1].type]} "
                        f"at {near[0][1].x}, {near[0][1].z}" if near and near[0][0] <= 3 else "")
                p.append(f"no tree of this seed grows at {t.x}, {t.z}: check the position" + hint)
            else:
                if t.type not in (r.type, "unknown"):
                    p.append(f"type: you entered {TYPE_NAME[t.type]}, it is {TYPE_NAME[r.type]}")
                elif t.type in ("oak", "birch"):
                    if t.height is not None and t.height != r.height:
                        p.append(f"height: you entered {t.height}, it is {r.height}")
                    for li in range(3):
                        for ci in range(4):
                            a, b = t.leaves[li * 4 + ci], r.leaves[li * 4 + ci]
                            if a in "01" and a != b:
                                p.append(f"{LAYERS[li].lower()} layer, {CORNER_NAME[ci]} corner: you entered "
                                         f"{'a leaf' if a == '1' else 'no leaf'}, the tree has "
                                         f"{'a leaf' if b == '1' else 'none'}"
                                         + (" (a leaf seen there belongs to another tree)" if a == "1" else
                                            " (something took its place, or it is hidden)"))
                elif t.type in ("spruce", "pine"):
                    if t.height is not None and t.height != r.height:
                        p.append(f"trunk: you entered {t.height} logs, it has {r.height}")
                    labels = dict((k, l) for k, l, _r in (SPRUCE_ATTRS if t.type == "spruce" else PINE_ATTRS))
                    for k, v in (t.attrs or {}).items():
                        if v is not None and r.attrs.get(k) != v:
                            p.append(f"{labels.get(k, k).lower()}: you entered {v}, it is {r.attrs.get(k)}")
                elif t.type == "fancy" and t.height is not None and r.height is not None and r.height >= 0 \
                        and r.height not in fancy_heights(int(t.height)):
                    p.append(f"trunk: you entered {t.height} logs, it has about {FANCY_VISIBLE.get(r.height, '?')}")
            if p:
                out["ok"] = False
            probs[i] = p
    out["trees"] = sorted(probs.items())
    return out


TYPE_NAME = {"oak": "oak", "birch": "birch", "fancy": "fancy oak", "spruce": "spruce", "pine": "pine",
             "unknown": "unknown"}
CORNER_NAME = ("north-west", "south-west", "north-east", "south-east")


# ── world seeds ────────────────────────────────────────────────────────────────
def random_world_seeds(ss: int) -> list:
    """the world seeds java.util.Random.nextLong() can give with these 48 low bits
    (a seed left blank when the world was made)"""
    ss &= M48
    inv = pow(MULT, -1, 1 << 48)
    try:                                         # (06/11) vectorised: ~1 ms instead of ~60
        import numpy as np
        lo = ss & 0xFFFFFFFF
        k = np.arange(1 << 16, dtype=np.uint64)
        with np.errstate(over="ignore"):
            s2 = np.uint64(lo << 16) | k
            s1 = ((s2 - np.uint64(0xB)) * np.uint64(inv)) & np.uint64(M48)
            hi = s1 >> np.uint64(16)
            lo_u = np.uint64(lo if lo < (1 << 31) else lo + (1 << 64) - (1 << 32))
            v = (hi << np.uint64(32)) + lo_u
        hit = v[(v & np.uint64(M48)) == np.uint64(ss)]
        return [int(x) - (1 << 64) if int(x) >> 63 else int(x) for x in hit]
    except ImportError:
        pass
    out = []
    lo = ss & 0xFFFFFFFF
    lo_s = lo - (1 << 32) if lo >> 31 else lo
    for k in range(1 << 16):
        s2 = (lo << 16) | k                     # state of the 2nd next(32)
        s1 = ((s2 - 0xB) * inv) & M48
        hi = s1 >> 16
        hi_s = hi - (1 << 32) if hi >> 31 else hi
        v = ((hi_s << 32) + lo_s) & ((1 << 64) - 1)
        if v & M48 == ss:
            out.append(v - (1 << 64) if v >> 63 else v)
    return out


def _biome_check(scene: Scene):
    import cubiomes_wrap as cw
    mc = VERSIONS[scene.version][3]
    chunks = list(scene.chunks())
    want = {ch: BIOMES[scene.biome(ch)][2] for ch in chunks}
    g = cw.CubiomesLib()
    sv, spos = None, {}
    if scene.structures:                     # (06/11) every structure must be able to spawn
        import struct_crack as stc
        sv = stc.viable_check(scene.structures, mc)

    def ok(seed):
        if chunks:
            g.setup_and_seed(mc, seed, large_biomes=False)
            if not all(g.get_biome_at(cx * 16 + 8, cz * 16 + 8, 4) in want[(cx, cz)] for cx, cz in chunks):
                return False
        if sv is not None:
            k = seed & M48
            if k not in spos:
                spos.clear()
                spos[k] = stc.positions(k, scene.structures, mc)
            return sv(seed, spos[k])
        return True
    return ok


def random_matches(ss: int, scene: Scene) -> list:
    """the world seeds a blank seed field can give for structure seed ss (nextLong)
    whose biomes are the entered ones"""
    ok = _biome_check(scene)
    return [s for s in random_world_seeds(ss) if ok(s)]


def world_seeds(ss: int, scene: Scene, on_progress=None, stop=None, limit=1 << 16) -> list:
    """the world seeds (64 bits) of structure seed ss whose biome is the entered one
    at every chunk of the scene (decoration biome, chunk centre), and where every
    structure entered can spawn (biomes): [(seed, random)]"""
    rnd = set(random_world_seeds(ss))
    out = []
    ok = _biome_check(scene)

    for s in sorted(rnd):
        if ok(s):
            out.append((s, True))
    for hi in range(1 << 16):
        if stop is not None and stop.is_set():
            break
        s = (hi << 48) | (ss & M48)
        s = s - (1 << 64) if s >> 63 else s
        if s in rnd:
            continue
        if ok(s):
            out.append((s, False))
            if len(out) >= limit:
                break
        if on_progress and hi % 1024 == 0:
            on_progress(hi / 65536.0)
    return out


def _fmt_t(s: float) -> str:
    s = max(0, int(s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60:02d} s"
    return f"{s // 3600} h {(s % 3600) // 60:02d} min"


def _tree_seconds(device: str = "auto", threads: int = 0) -> float:
    pts = 2.0 ** 40
    if device != "cpu" and gpu()[0] is not None:
        return pts / 3e10
    return pts * 9e-9 / max(1, threads or (os.cpu_count() or 2))


def trees_ready(scene: Scene) -> bool:
    bc = best_chunk(scene) if scene.version in TREE_VERSIONS else None
    return bc is not None and bc[1] >= BITS_MINIMUM - 8


# (06/11) structures: below this much information the false seeds get too many
STRUCT_MIN_BITS = 28.0
STRUCT_MAX_SECONDS = 4 * 3600


def structures_ready(scene: Scene, threads: int = 0, device: str = "auto") -> bool:
    if not scene.structures:
        return False
    import struct_crack as stc
    mc = VERSIONS[scene.version][3]
    if any(p[0] == "error" for p in stc.problems(scene.structures, mc)):
        return False
    return stc.effective_bits(scene.structures, mc) >= STRUCT_MIN_BITS and \
        stc.search_seconds_device(scene.structures, mc, device, threads) <= STRUCT_MAX_SECONDS


def crack_mode(scene: Scene, device: str = "auto", threads: int = 0):
    """"trees" (search from the main chunk's trees, the structures then filter),
    "structures" (lifting search, the trees then filter) or None"""
    t_ok, s_ok = trees_ready(scene), structures_ready(scene, threads, device)
    if t_ok and s_ok:
        import struct_crack as stc
        st = stc.search_seconds_device(scene.structures, VERSIONS[scene.version][3], device, threads)
        return "structures" if st <= _tree_seconds(device, threads) else "trees"
    return "trees" if t_ok else ("structures" if s_ok else None)


def estimate(scene: Scene, device: str = "auto", threads: int = 0) -> str:
    """rough run time"""
    if crack_mode(scene, device, threads) == "structures":
        import struct_crack as stc
        return _fmt_t(stc.search_seconds_device(scene.structures, VERSIONS[scene.version][3], device, threads))
    return _fmt_t(_tree_seconds(device, threads))


def _crack_structures(scene: Scene, threads: int, on_progress, stop, device="auto") -> CrackResult:
    """structures → structure seeds (lifting), then the trees and pillars filter"""
    import struct_crack as stc
    mc = VERSIONS[scene.version][3]
    on_gpu = device != "cpu" and stc.gpu()[0] is not None
    if device == "gpu" and not on_gpu:
        raise RuntimeError(stc.gpu()[1])
    res = CrackResult(device="GPU" if on_gpu else f"CPU · {threads} threads", mode="structures")
    t0 = time.time()

    def prog(f, eta):
        if on_progress:
            el = time.time() - t0
            on_progress(f, f"structures · {_fmt_t(el)}" + (f" · ~{_fmt_t(eta)} left" if eta else ""))
    seeds = stc.search(scene.structures, mc, threads, stop=stop, on_progress=prog,
                       device="auto" if on_gpu else "cpu")
    res.stopped = bool(stop is not None and stop.is_set())
    res.candidates = len(seeds)
    if scene.trees and scene.version in TREE_VERSIONS:
        for ch in scene.chunks():
            if not seeds:
                break
            seeds = check(scene, seeds, [ch])
            res.checked[tuple(ch)] = len(seeds)
    res.all_seeds = seeds
    finish(res, scene)
    res.seconds = time.time() - t0
    return res
