"""
loot_native.py — (07/11) chest loot in C (cubiomes_src/loot_vm.c): the game's
fill, ~100× faster than loot_engine.py, draw for draw the same.

    fill_chest(table, loot_seed, version)  → (slots, approx) like loot_engine.fill_chest
                                             (the stacks: item, count, ench, potion —
                                             no display text), or None when this table
                                             needs the Python engine
    available()                            → the library loads (compiled on first use)
    enable(True)                           → loot_engine.fill_chest uses it (the loot
                                             finder's worker processes)

A table is compiled once per version into int32 words (see loot_vm.c). What the
VM does not do falls back to loot_engine (whole table): nested loot tables,
alternatives / groups, location_check, an unknown function or number provider.
Validated against loot_engine.fill_chest on every table of every version
(tests: tools/loot/check_native.py).
"""
from __future__ import annotations

import ctypes
import os
import platform
import struct
import subprocess
import sys

import loot_engine as le

_SRC = ("cubiomes_src/loot_vm.c",)
_LIB = None
_LIB_ERR = None
_PROGS = {}                          # (version, table) → _Prog | None (Python engine)
_ITEMS, _ITEM_ID = [], {}
_ENCH, _ENCH_ID = [], {}
_POT, _POT_ID = [], {}
MAXE = 12


class Unsupported(Exception):
    pass


def _here():
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))


def _lib_name():
    return "lootvm.dll" if platform.system() == "Windows" else "liblootvm.so"


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
        _LIB_ERR = f"{_lib_name()} is missing"
        return None
    tmp = out + ".building"
    cmd = ["gcc", "-O2", "-shared", "-fPIC", "-fno-fast-math", "-ffp-contract=off",
           "-o", tmp, os.path.join(here, "cubiomes_src", "loot_vm.c"), "-lm"]
    if platform.system() == "Windows":
        cmd = [c for c in cmd if c != "-lm"] + ["-static-libgcc"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError:
        _LIB_ERR = "gcc not found"
        return None
    except subprocess.TimeoutExpired:
        _LIB_ERR = "gcc timed out"
        return None
    if r.returncode != 0:
        _LIB_ERR = "gcc failed: " + (r.stderr or b"").decode(errors="replace")[:800]
        return None
    try:
        os.replace(tmp, out)
    except OSError:
        return tmp
    return out


class _Stack(ctypes.Structure):
    _fields_ = [("item", ctypes.c_int32), ("count", ctypes.c_int32), ("potion", ctypes.c_int32),
                ("ne", ctypes.c_int32), ("ench", (ctypes.c_int32 * 2) * MAXE)]


def lib():
    global _LIB, _LIB_ERR
    if _LIB is not None:
        return _LIB or None
    p = os.path.join(_here(), _lib_name())
    if not os.path.exists(p) or _stale(p):
        q = _compile()
        if q is None and not os.path.exists(p):
            _LIB = False
            return None
        p = q or p
    try:
        L = ctypes.CDLL(p)
        L.lv_fill.restype = ctypes.c_int
        L.lv_fill.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        L.lv_fill_many.restype = None
        L.lv_fill_many.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        L.lv_count.restype = ctypes.c_int
        L.lv_count.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                               ctypes.c_void_p, ctypes.c_void_p]
        L.lv_layout.restype = ctypes.c_int
        L.lv_layout.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_void_p,
                                ctypes.c_int, ctypes.c_void_p]
        L.lv_xoro_skip.restype = None
        L.lv_xoro_skip.argtypes = [ctypes.c_void_p, ctypes.c_int64]
        if L.lv_version() < 1:
            raise RuntimeError("old lootvm")
    except Exception as e:                              # noqa: BLE001
        _LIB_ERR = f"cannot load {os.path.basename(p)}: {e}"
        _LIB = False
        return None
    _LIB = L
    return L


def available() -> bool:
    return lib() is not None


# ── the compiler ──────────────────────────────────────────────────────────────
def _intern(name, lst, ids):
    i = ids.get(name)
    if i is None:
        i = ids[name] = len(lst)
        lst.append(name)
    return i


def item_id(name):
    return _intern(name.replace("minecraft:", ""), _ITEMS, _ITEM_ID)


class _W:
    """the program being written"""

    def __init__(self):
        self.w = [0]                                    # word 0: never an offset

    def at(self):
        return len(self.w)

    def put(self, *xs):
        o = len(self.w)
        self.w.extend(int(x) for x in xs)
        return o

    def dbl(self, d):
        a, b = struct.unpack("<ii", struct.pack("<d", float(d)))
        self.w.extend((a, b))


def _prov(W, p, as_float=False):
    """a number provider → its offset"""
    if isinstance(p, bool):
        raise Unsupported("bool provider")
    if isinstance(p, (int, float)):
        if as_float:
            o = W.put(5)
            W.dbl(float(p))
            return o
        return W.put(1, int(p) if isinstance(p, int) else int(p // 1))
    t = le._typ(p, "type")
    if t in ("", "uniform") and "min" in p and "max" in p:
        lo, hi = p["min"], p["max"]
        if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
            raise Unsupported("nested bounds")
        if as_float:
            o = W.put(4)
            W.dbl(float(lo)); W.dbl(float(hi))
            return o
        return W.put(2, int(lo // 1), int(hi // 1))
    if t == "constant":
        v = p["value"]
        if as_float:
            o = W.put(5)
            W.dbl(float(v))
            return o
        return W.put(1, int(v))
    if t == "binomial":
        n, pr = p.get("n"), p.get("p")
        if not isinstance(n, (int, float)) or not isinstance(pr, (int, float)):
            raise Unsupported("binomial providers")
        o = W.put(3, int(n) if isinstance(n, int) else int(n // 1))
        W.dbl(float(pr))
        return o
    raise Unsupported(f"provider {t}")


def _cond(W, c):
    t = le._typ(c, "condition", "type")
    if t == "random_chance":
        ch = c.get("chance", 0)
        if not isinstance(ch, (int, float)):
            raise Unsupported("random_chance provider")
        o = W.put(1)
        W.dbl(float(ch))
        return o
    if t == "inverted":
        sub = _cond(W, c["term"])
        return W.put(2, sub)
    if t in ("all_of", "any_of", "alternative"):
        subs = [_cond(W, x) for x in c.get("terms", [])]
        return W.put(3 if t == "all_of" else 4, len(subs), *subs)
    raise Unsupported(f"condition {t}")


def _conds(W, o):
    cs = le._conditions(o)
    if not cs:
        return 0
    offs = [_cond(W, c) for c in cs]
    return W.put(len(offs), *offs)


_NOOP = {"set_name", "set_lore", "set_components", "set_custom_data", "set_nbt", "toggle_tooltips",
         "filtered", "discard", "set_book_cover", "set_written_book_pages", "set_writable_book_pages",
         "set_banner_pattern", "set_fireworks", "set_firework_explosion", "set_custom_model_data",
         "set_attributes", "limit_count", "furnace_smelt", "copy_name", "copy_components",
         "copy_custom_data", "explosion_decay"}


def _ench_levels_data(W, item, f, version):
    import loot_enchant as lx
    E = lx.env(version)
    ev = lx.enchantability(item)
    opts = E.options(f.get("options")) if not E.legacy else None
    treasure = bool(f.get("treasure", False))
    lv = f.get("levels", 0)
    if isinstance(lv, (int, float)):
        lvmax = int(lv // 1) if not isinstance(lv, int) else lv
    else:
        lvmax = int(lv.get("max", lv.get("value", 0)) // 1) if isinstance(lv.get("max", lv.get("value", 0)), (int, float)) else 60
    cmax = int((lvmax + 1 + 2 * (max(ev, 0) // 4)) * 1.16) + 3
    per = [lx.available(item, c, version, opts, treasure) if ev > 0 else [] for c in range(cmax + 1)]
    ids = []
    pos = {}
    for av in per:
        for e, _l in av:
            if e.id not in pos:
                pos[e.id] = len(ids)
                ids.append(e)
    if len(ids) > 64:
        raise Unsupported("too many enchantments")
    nl = len(ids)
    o = W.put(ev, -1, cmax, nl, *[_intern(e.id, _ENCH, _ENCH_ID) for e in ids])
    W.put(*[1 if E.compatible(a, b) else 0 for a in ids for b in ids]) if nl else None
    table = W.put(*([0] * (cmax + 1)))
    for c, av in enumerate(per):
        flat = []
        for e, l in av:
            flat += [pos[e.id], l, int(e.weight)]
        W.w[table + c] = W.put(len(av), *flat)
    return o


def _fns(W, chain, item0, version):
    """the functions of a stack (entry's, pool's, table's): offset, final item"""
    import loot_enchant as lx
    item = item0
    offs = []
    for f, t in chain:
        co = _conds(W, f)
        conditional = co != 0
        if t == "set_count":
            p = _prov(W, f["count"])
            offs.append(W.put(1, co, 1 if f.get("add") else 0, p))
        elif t == "set_damage":
            p = _prov(W, f["damage"], as_float=True)
            offs.append(W.put(2, co, p))
        elif t == "enchant_randomly":
            cand = lx.randomly_candidates(item, f, version)
            if not cand:
                continue                                 # nothing drawn, item kept
            conv = item_id("enchanted_book") if item == "book" else -1
            flat = []
            for e in cand:
                flat += [_intern(e.id, _ENCH, _ENCH_ID), int(e.max_level)]
            offs.append(W.put(3, co, conv, len(cand), *flat))
            if item == "book":
                if conditional:
                    raise Unsupported("conditional book enchanting")
                item = "enchanted_book"
        elif t == "enchant_with_levels":
            p = _prov(W, f.get("levels", 0))
            data = _ench_levels_data(W, item, f, version)
            conv = item_id("enchanted_book") if item == "book" else -1
            offs.append(W.put(4, co, conv, p, data))
            if item == "book":
                if conditional:
                    raise Unsupported("conditional book enchanting")
                item = "enchanted_book"
        elif t == "set_potion":
            offs.append(W.put(5, co, _intern(str(f.get("id", "")).replace("minecraft:", ""), _POT, _POT_ID)))
        elif t == "set_stew_effect":
            ps = [_prov(W, e.get("duration", 0)) for e in f.get("effects", [])]
            offs.append(W.put(6, co, len(ps), *ps))
        elif t == "set_instrument":
            offs.append(W.put(7, co))
        elif t == "set_ominous_bottle_amplifier":
            offs.append(W.put(8, co, _prov(W, f["amplifier"])))
        elif t == "set_enchantments":
            flat = []
            for k, v in (f.get("enchantments") or {}).items():
                if isinstance(v, bool):
                    raise Unsupported("bool level")
                p = W.put(1, v) if isinstance(v, int) else (_prov(W, v) if isinstance(v, dict) else W.put(1, 1))
                flat += [_intern(k.replace("minecraft:", ""), _ENCH, _ENCH_ID), p]
            conv = item_id("enchanted_book") if item == "book" else -1
            offs.append(W.put(9, co, conv, len(flat) // 2, *flat))
            if item == "book":
                if conditional:
                    raise Unsupported("conditional book enchanting")
                item = "enchanted_book"
        elif t in ("exploration_map", "set_item"):
            new = "filled_map" if t == "exploration_map" else str(f.get("item", item)).replace("minecraft:", "")
            if conditional and new != item:
                raise Unsupported("conditional item change")
            offs.append(W.put(10, co, item_id(new)))
            item = new
        elif t in _NOOP:
            if co:
                offs.append(W.put(0, co))               # its conditions still draw
        else:
            raise Unsupported(f"function {t}")
    if not offs:
        return 0, item
    return W.put(len(offs), *offs), item


def compile_table(version, name):
    tab = le.get_table(version, name)
    if tab is None:
        raise Unsupported("no table")
    W = _W()
    pools = tab.get("pools", [])
    head = W.put(len(pools), *([0] * len(pools)))
    tab_fns = le._functions(tab)
    for pi, p in enumerate(pools):
        co = _conds(W, p)
        rolls = _prov(W, p.get("rolls", 1))
        entries = p.get("entries", [])
        eoffs = []
        pool_fns = le._functions(p)
        for e in entries:
            t = le._typ(e, "type")
            ec = _conds(W, e)
            wt = int(e.get("weight", 1))
            if t == "empty":
                eoffs.append(W.put(0, wt, ec, 0, 0, 1))
            elif t == "item":
                it = e["name"].replace("minecraft:", "")
                fo, final = _fns(W, le._functions(e) + pool_fns + tab_fns, it, version)
                eoffs.append(W.put(1, wt, ec, item_id(it), fo, le.max_stack(final)))
            else:
                raise Unsupported(f"entry {t}")
        po = W.put(co, rolls, len(eoffs), *eoffs)
        W.w[head + 1 + pi] = po
    W.w[0] = head
    import array
    return array.array("i", W.w)


class _Prog:
    __slots__ = ("words", "buf")

    def __init__(self, words):
        self.words = words
        self.buf = (ctypes.c_int32 * len(words)).from_buffer(words)


def program(version, name):
    k = (version, name)
    if k not in _PROGS:
        try:
            _PROGS[k] = _Prog(compile_table(version, name))
        except Unsupported:
            _PROGS[k] = None
        except Exception:                                # noqa: BLE001
            _PROGS[k] = None
    return _PROGS[k]


_SLOT = (ctypes.c_int32 * 64)()
_OUT = (_Stack * 64)()
_FLAGS = ctypes.c_int()


def fill_raw(prog, seed, size=27):
    """[(slot, item name, count, potion|None, [(ench, lvl)])], flags"""
    L = lib()
    n = L.lv_fill(ctypes.addressof(prog.buf), int(seed), size, ctypes.addressof(_SLOT),
                  ctypes.addressof(_OUT), ctypes.byref(_FLAGS))
    return n, _FLAGS.value


def fill_chest(table, loot_seed, version, size=27, world=None):
    """loot_engine.fill_chest in C, or None (this table / call needs the engine)"""
    if world or loot_seed == 0 or lib() is None:
        return None
    name = table.replace("minecraft:", "")
    if name.startswith("chests/"):
        name = name[len("chests/"):]
    prog = program(version, name)
    if prog is None:
        return None
    n, flags = fill_raw(prog, loot_seed, size)
    if n < 0:
        return None
    out = [None] * size
    for s in range(size):
        i = _SLOT[s]
        if i < 0:
            continue
        S = _OUT[i]
        st = le.Stack(_ITEMS[S.item], S.count)
        if S.potion >= 0:
            st.potion = _POT[S.potion]
        if S.ne:
            st.ench = [(_ENCH[S.ench[j][0]], S.ench[j][1]) for j in range(S.ne)]
        out[s] = st
    approx = []
    if flags & 2:
        approx.append("over-filled container")
    return out, sorted(set(approx))


# ── the loot finder: chests judged in C (loot_finder.judge) ──────────────────
_WCACHE = {}


def _wants(items):
    """the wanted items as int32 words (item, potion, ench, level)"""
    k = tuple((w["item"], w.get("potion"), w.get("ench"), int(w.get("ench_lvl") or 1)) for w in items)
    a = _WCACHE.get(k)
    if a is None:
        words = []
        for it, pot, en, lv in k:
            # interned: a table compiled later gives the same ids
            words += [item_id(it), (_intern(pot, _POT, _POT_ID) if pot else -1),
                      (_intern(en, _ENCH, _ENCH_ID) if en else -1), lv]
        a = (ctypes.c_int32 * max(1, len(words)))(*words)
        if len(_WCACHE) > 64:
            _WCACHE.clear()
        _WCACHE[k] = a
    return a


class NativeSlots:
    """a chest's slots, filled by the VM only when looked at: the finder asks the
    counts / the layout in C; reading it as a list builds the stacks"""
    __slots__ = ("prog", "seed", "size", "version", "table", "_list", "_counts")

    def __init__(self, prog, seed, size, version, table):
        self.prog, self.seed, self.size, self.version, self.table = prog, int(seed), size, version, table
        self._list = None
        self._counts = {}

    def _l(self):
        if self._list is None:
            r = fill_chest(self.table, self.seed, self.version, self.size)
            self._list = r[0] if r is not None else le.fill_chest_py(self.table, self.seed, self.version,
                                                                     self.size)[0]
        return self._list

    def __len__(self):
        return self.size

    def __iter__(self):
        return iter(self._l())

    def __getitem__(self, i):
        return self._l()[i]

    def counts(self, items):
        """[how many of each wanted item]"""
        k = id(items)
        c = self._counts.get(k)
        if c is None:
            n = len(items)
            out = (ctypes.c_int32 * max(1, n))()
            lib().lv_count(ctypes.addressof(self.prog.buf), self.seed, self.size, n,
                           ctypes.addressof(_wants(items)), ctypes.addressof(out))
            c = self._counts[k] = list(out)[:n]
        return c

    def layout_ok(self, lay, items):
        cells = []
        for c in lay[:self.size]:
            if c is None:
                cells += [-1, 0]
            elif c == "empty":
                cells += [-2, 0]
            elif c == "filled":
                cells += [-3, 0]
            elif isinstance(c, (list, tuple)):
                k = int(c[0])
                if not 0 <= k < len(items):
                    return False
                cells += [k, int(c[1] or 0)]
            else:
                k = int(c)
                if not 0 <= k < len(items):
                    return False
                cells += [k, 0]
        arr = (ctypes.c_int32 * max(2, len(cells)))(*cells)
        return bool(lib().lv_layout(ctypes.addressof(self.prog.buf), self.seed, self.size,
                                    ctypes.addressof(_wants(items)), len(cells) // 2,
                                    ctypes.addressof(arr)))


def _lazy_fill(table, loot_seed, version, size=27, world=None):
    if world or loot_seed == 0:
        return None
    name = table.replace("minecraft:", "")
    if name.startswith("chests/"):
        name = name[len("chests/"):]
    prog = program(version, name)
    if prog is None:
        return None
    return NativeSlots(prog, loot_seed, size, version, name), []


def enable(on=True):
    """this process: loot_engine.fill_chest returns NativeSlots (the loot finder's
    workers — the chests are judged in C, built only when read)"""
    le._NATIVE = _lazy_fill if (on and available()) else None
    return le._NATIVE is not None


_XS = (ctypes.c_uint64 * 2)()


def xoro_skip(r, n):
    """n steps of a stronghold_eyes.Xrsr128 (C when the library is there)"""
    L = lib()
    if L is None or not hasattr(L, "lv_xoro_skip"):
        for _ in range(n):
            r._xrsr_long()
        return
    _XS[0], _XS[1] = r.lo, r.hi
    L.lv_xoro_skip(ctypes.addressof(_XS), int(n))
    r.lo, r.hi = int(_XS[0]), int(_XS[1])
