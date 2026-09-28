"""
qtui/session_model.py — the conditions of a custom session, as a TREE.

Storage: a flat list of condition dicts in the engine format (custom_search /
.session). Each dict gets a private stable id ``_uid`` and the parent link is
kept as ``_parent`` (uid, or 0 = world origin). The engine's ``relative``
(1-based position of the parent in the list) is recomputed on export, so
moving / deleting / pasting conditions can never corrupt the links again.

The flat order is always a depth-first walk of the tree (parents before their
children), which is also the order shown in the UI.
"""
from __future__ import annotations

import copy
import itertools

from PySide6.QtCore import QObject, Signal

from condition_types import CONDITION_TYPES, TYPE_COLORS

_uid = itertools.count(1)

TYPE_INFO = {t["key"]: t for t in CONDITION_TYPES}
# types that exist in imported cubiomes-viewer sessions but have no editor form
IMPORTED_TYPES = {
    "stronghold": "Stronghold (any)", "fortress": "Nether fortress", "bastion": "Bastion",
    "ruined_portal_nether": "Ruined portal (Nether)", "end_gateway": "End gateway",
    "end_city": "End city", "mineshaft": "Mineshaft", "desert_well": "Desert well",
    "climate_noise": "Climate noise (ranges)",
}
# types that take dependent conditions (children) meaningfully
CONTAINERS = {"spiral", "logic_or", "logic_not", "scale_to_nether", "scale_to_overworld"}
NO_AREA = {"scale_to_nether", "scale_to_overworld", "logic_or", "logic_not", "crunchiness"}
NO_INSTANCES = {"spiral", "scale_to_nether", "scale_to_overworld", "crunchiness", "logic_or",
                "logic_not", "first_stronghold", "spawn", "climate_minmax", "climate_center",
                "climate_noise", "climate_extreme", "noise_sample", "biome_filter",
                "biome_sample", "height", "precise_y"}


def type_name(t: str) -> str:
    info = TYPE_INFO.get(t)
    if info:
        return info["name"]
    return IMPORTED_TYPES.get(t, t.replace("_", " ").capitalize())


def type_color(t: str) -> str:
    from . import struct_icons
    if struct_icons.known(t):                  # same colour as its icon everywhere
        return struct_icons.color(t)
    return TYPE_COLORS.get(t, "#9aa3b8")


def type_icon(t: str, size: int = 16):
    """Structure icon for structure types, colour dot for the others."""
    from . import struct_icons
    if struct_icons.known(t):
        return struct_icons.icon(t, size)
    return None


def icon_type(c: dict) -> str:
    """The icon / colour key of a condition: its type, except an abandoned-village
    filter, which shows the abandoned (zombie) village icon."""
    t = c.get("type", "")
    if t == "village" and (c.get("params") or {}).get("variant") == "abandoned":
        return "zombie_village"
    return t


def display_name(c: dict) -> str:
    lab = (c.get("label") or "").strip()
    return lab or type_name(c.get("type", ""))


def area_text(c: dict) -> str:
    """Short human description of the search area / main parameter."""
    t = c.get("type", "")
    p = c.get("params", {})
    if t in NO_AREA:
        if t == "crunchiness":
            return f"score {'≤' if int(p.get('mode', 0)) == 0 else '≥'} {p.get('threshold', 0.1):g}"
        return ""
    if t == "spiral":
        base = f"step {p.get('step', 16)}"
    else:
        base = ""
    rmax = int(c.get("rmax", 0) or 0)
    if not rmax and c.get("area_mode") == 0 and int(c.get("radial", 0) or 0) > 0:
        rmax = int(c["radial"]) + 1
    if t == "first_stronghold":
        bits = []
        if int(p.get("min_eyes", 0) or 0) > 0:
            bits.append(f"{'=' if p.get('exact_eyes') else '≥'}{p['min_eyes']} eyes")
        if p.get("maxy_enabled"):
            bits.append(f"top Y ≥ {p.get('min_max_y', 60)}")
        if p.get("corrupt_enabled"):
            bits.append("corruptible")
        return " · ".join(bits) or "exists"
    if rmax:
        zone = f"r ≤ {rmax - 1}"
    else:
        x1, z1, x2, z2 = (int(c.get(k, 0)) for k in ("x1", "z1", "x2", "z2"))
        if x1 == -x2 and z1 == -z2 and x2 - x1 == z2 - z1:
            zone = f"□ {x2 - x1}"
        else:
            zone = f"({x1}, {z1}) → ({x2}, {z2})"
    if t in ("height", "precise_y"):
        zone += f" · Y {p.get('y_min', '?')}…{p.get('y_max', '?')}"
    inst = c.get("instances", 1)
    if t not in NO_INSTANCES:
        if inst == 0:
            zone = "none in " + zone
        elif inst and int(inst) > 1:
            zone += f" · ×{inst}"
    return (base + " · " + zone) if base else zone


def new_condition(t: str) -> dict:
    """A fresh condition of type t with the editor defaults."""
    info = TYPE_INFO.get(t, {})
    params = {}
    for k, spec in info.get("params", {}).items():
        d = spec.get("default")
        if spec.get("type") == "dual_height_slider":
            lo, hi = d if d else (spec.get("min", -64), spec.get("max", 320))
            params["y_min"], params["y_max"] = lo, hi
            continue
        params[k] = copy.deepcopy(d)
    c = {"type": t, "label": "", "enabled": True, "relative": 0, "params": params,
         "instances": 1, "ignore_ref": False}
    if t in NO_AREA:
        c.update(area_mode=1, radial=0, rmax=0, x1=0, z1=0, x2=0, z2=0, square_side=0)
    elif info.get("no_area"):
        c.update(area_mode=1, square_side=60_000_000, radial=0, rmax=0,
                 x1=-30_000_000, z1=-30_000_000, x2=30_000_000, z2=30_000_000)
    else:
        r = 1024 if t != "spiral" else 2048
        c.update(area_mode=0, radial=r, rmax=r + 1, x1=-r, z1=-r, x2=r, z2=r, square_side=0)
    if info.get("requires_at"):
        c["at_x"], c["at_z"] = 0, 0
    return c


class SessionModel(QObject):
    """Tree of conditions + undo-free, signal-driven editing."""
    changed = Signal()                     # structure or content changed
    selectionChanged = Signal(int)         # uid (0 = none)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.conds: list[dict] = []
        self.name = "Untitled session"
        self.path = None
        self.dirty = False

    # ── ids / tree helpers ──────────────────────────────────────────────────
    def get(self, uid):
        for c in self.conds:
            if c["_uid"] == uid:
                return c
        return None

    def children(self, uid):
        return [c for c in self.conds if c.get("_parent", 0) == uid]

    def roots(self):
        return self.children(0)

    def depth(self, uid):
        d, c = 0, self.get(uid)
        while c and c.get("_parent", 0):
            d += 1
            c = self.get(c["_parent"])
        return d

    def subtree(self, uid):
        out = []

        def walk(u):
            for ch in self.children(u):
                out.append(ch["_uid"])
                walk(ch["_uid"])
        walk(uid)
        return out

    def ancestors_enabled(self, uid):
        c = self.get(uid)
        while c:
            if not c.get("enabled", True):
                return False
            c = self.get(c.get("_parent", 0)) if c.get("_parent", 0) else None
        return True

    def _reorder(self):
        """Flat order = depth-first walk (stable within siblings)."""
        order = []

        def walk(u):
            for ch in [c for c in self.conds if c.get("_parent", 0) == u]:
                order.append(ch)
                walk(ch["_uid"])
        walk(0)
        # orphans (should not happen) are appended as roots
        seen = {id(c) for c in order}
        for c in self.conds:
            if id(c) not in seen:
                c["_parent"] = 0
                order.append(c)
        self.conds = order

    def _touch(self, what="Edit"):
        self._reorder()
        self.dirty = True
        self._push(what)
        self.changed.emit()

    # ── undo / redo (27/09): a snapshot of the tree after every change ──────
    HISTORY = 300

    def _snap(self):
        return [copy.deepcopy(c) for c in self.conds]

    def _reset_history(self):
        self._hist = [(self._snap(), "")]
        self._hi = 0

    def _push(self, what):
        if not hasattr(self, "_hist"):
            self._reset_history()
            return
        del self._hist[self._hi + 1:]              # a new change drops the redo branch
        self._hist.append((self._snap(), what))
        if len(self._hist) > self.HISTORY:
            self._hist.pop(0)
        self._hi = len(self._hist) - 1

    def can_undo(self):
        return getattr(self, "_hi", 0) > 0

    def can_redo(self):
        return hasattr(self, "_hist") and self._hi < len(self._hist) - 1

    def undo_text(self):
        return self._hist[self._hi][1] if self.can_undo() else ""

    def redo_text(self):
        return self._hist[self._hi + 1][1] if self.can_redo() else ""

    def undo(self):
        if not self.can_undo():
            return ""
        what = self._hist[self._hi][1]
        self._hi -= 1
        self.conds = [copy.deepcopy(c) for c in self._hist[self._hi][0]]
        self.dirty = True
        self.changed.emit()
        return what

    def redo(self):
        if not self.can_redo():
            return ""
        self._hi += 1
        self.conds = [copy.deepcopy(c) for c in self._hist[self._hi][0]]
        self.dirty = True
        self.changed.emit()
        return self._hist[self._hi][1]

    # ── import / export ─────────────────────────────────────────────────────
    def load(self, conditions, name=None, path=None):
        """Replace the session with engine-format conditions (links = relative)."""
        conds = [copy.deepcopy(c) for c in conditions]
        uids = [next(_uid) for _ in conds]
        for i, c in enumerate(conds):
            c["_uid"] = uids[i]
            r = int(c.get("relative", 0) or 0)
            c["_parent"] = uids[r - 1] if 0 < r <= len(conds) and r != i + 1 else 0
            for k in ("_orig_relative", "_rel_in_batch", "parent_idx"):
                c.pop(k, None)
        # break cycles: any node whose ancestor chain loops is re-rooted
        by = {c["_uid"]: c for c in conds}
        for c in conds:
            seen, p = {c["_uid"]}, c.get("_parent", 0)
            while p:
                if p in seen:
                    c["_parent"] = 0
                    break
                seen.add(p)
                p = by[p].get("_parent", 0) if p in by else 0
        self.conds = conds
        self.name = name or self.name
        self.path = path
        self._reorder()
        self.dirty = False
        self._reset_history()
        self.changed.emit()

    def export(self, only=None):
        """Engine-format list (deep copies): `relative` = 1-based parent position.
        `only`: optional set of uids (their links outside the set go to origin)."""
        src = [c for c in self.conds if only is None or c["_uid"] in only]
        pos = {c["_uid"]: i + 1 for i, c in enumerate(src)}
        out = []
        for c in src:
            d = {k: copy.deepcopy(v) for k, v in c.items() if not k.startswith("_")}
            d["relative"] = pos.get(c.get("_parent", 0), 0)
            out.append(d)
        return out

    # ── editing ─────────────────────────────────────────────────────────────
    def add(self, cond, parent=0, after=None):
        c = copy.deepcopy(cond)
        c["_uid"] = next(_uid)
        c["_parent"] = parent if (parent and self.get(parent)) else 0
        if after and self.get(after):
            i = self.conds.index(self.get(after)) + 1
            self.conds.insert(i, c)
        else:
            self.conds.append(c)
        self._touch("Add a condition")
        return c["_uid"]

    def update(self, uid, cond):
        c = self.get(uid)
        if c is None:
            return
        keep = {k: c[k] for k in ("_uid", "_parent")}
        c.clear()
        c.update(copy.deepcopy({k: v for k, v in cond.items() if not k.startswith("_")}))
        c.update(keep)
        self._touch("Edit a condition")

    def remove(self, uids):
        drop = set()
        for u in uids:
            drop.add(u)
            drop.update(self.subtree(u))
        self.conds = [c for c in self.conds if c["_uid"] not in drop]
        self._touch(f"Remove {len(drop)} condition{'s' if len(drop) != 1 else ''}")

    def set_enabled(self, uid, on):
        c = self.get(uid)
        if c is not None:
            c["enabled"] = bool(on)
            self._touch("Enable a condition" if on else "Disable a condition")

    def reparent(self, uid, new_parent, before=None):
        """Move uid (with its subtree) under new_parent (0 = root), optionally
        before sibling `before`. Refuses to move a node into its own subtree."""
        c = self.get(uid)
        if c is None or uid == new_parent or new_parent in self.subtree(uid):
            return False
        c["_parent"] = new_parent if self.get(new_parent) else 0
        self.conds.remove(c)
        if before and self.get(before):
            self.conds.insert(self.conds.index(self.get(before)), c)
        else:
            self.conds.append(c)
        self._touch("Move a condition")
        return True

    def move(self, uid, delta):
        """Move among its siblings (delta = -1 up, +1 down)."""
        c = self.get(uid)
        if c is None:
            return
        sib = self.children(c.get("_parent", 0))
        i = sib.index(c)
        j = i + delta
        if not 0 <= j < len(sib):
            return
        a, b = self.conds.index(sib[i]), self.conds.index(sib[j])
        self.conds[a], self.conds[b] = self.conds[b], self.conds[a]
        self._touch("Reorder")

    def duplicate(self, uid):
        c = self.get(uid)
        if c is None:
            return 0
        mapping = {}
        new_uid = None
        for u in [uid] + self.subtree(uid):
            src = self.get(u)
            d = copy.deepcopy(src)
            d["_uid"] = next(_uid)
            mapping[u] = d["_uid"]
            d["_parent"] = mapping.get(src.get("_parent", 0), src.get("_parent", 0))
            if u == uid:
                new_uid = d["_uid"]
                if d.get("label"):
                    d["label"] = d["label"] + " (copy)"
            self.conds.append(d)
        self._touch("Duplicate")
        return new_uid

    def clear(self):
        self.conds = []
        self._touch("Clear")

    # ── clipboard (cubiomes-viewer compatible hex + SSA extension) ──────────
    def copy_text(self, uids):
        """Selected conditions (+ their subtrees) as text: one '#Cond:' hex line
        (readable by cubiomes-viewer) followed by '#SSAx:' (exact SSA copy)."""
        import json
        from cubiomes_session_writer import _SSA_ONLY, _SSA_TO_F, _encode_condition
        keep = []
        for u in uids:
            keep.append(u)
            keep.extend(self.subtree(u))
        sel = [c["_uid"] for c in self.conds if c["_uid"] in set(keep)]
        conds = self.export(only=set(sel))
        lines = []
        for i, c in enumerate(conds, start=1):
            full = dict(c, save=i)
            if c.get("type") in _SSA_TO_F and c.get("type") not in _SSA_ONLY:
                try:
                    lines.append("#Cond: " + _encode_condition(full, i))
                    lines.append("#SSAx: " + json.dumps(full, separators=(",", ":")))
                    continue
                except Exception:
                    pass
            lines.append("#SSA: " + json.dumps(full, separators=(",", ":")))
        return "\n".join(lines)

    def paste_text(self, text, parent=0):
        """Append pasted conditions (cubiomes-viewer hex or SSA text).
        Links inside the pasted block are kept; roots go under `parent`."""
        from cubiomes_session import parse_conditions_from_text
        r = parse_conditions_from_text(text)
        conds = r.get("conditions", [])
        if not conds:
            return 0, r.get("unsupported", [])
        uids = [next(_uid) for _ in conds]
        for i, c in enumerate(conds):
            rel = int(c.get("relative", 0) or 0) if c.get("_rel_in_batch", True) else 0
            for k in ("_orig_relative", "_rel_in_batch", "parent_idx", "save"):
                c.pop(k, None)
            c["_uid"] = uids[i]
            c["_parent"] = uids[rel - 1] if 0 < rel <= len(conds) and rel != i + 1 else parent
            self.conds.append(c)
        self._touch("Paste")
        return len(conds), r.get("unsupported", [])
