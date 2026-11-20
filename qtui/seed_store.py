"""
qtui/seed_store.py — (19/11) the Seed Library's data: the seeds kept by the user, the
libraries (shelves) they are sorted into, and each seed's board (blocks placed freely:
screenshots, mini-maps, notes, coordinates).

Kept in <data dir>/seed_library/: library.json (everything but the pictures) and
images/<block id>.png (the screenshots, as added), thumbs/ (covers, made again when lost).
Every change is saved a moment later (one write for a burst of changes); nothing is
ever deleted from disk at once: a removed seed's pictures go to seed_library/_trash.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import uuid

from PySide6.QtCore import QObject, QTimer, Signal

SHELF_COLORS = ("#f5b942", "#5aa8ff", "#3fb47a", "#e5689a", "#a07aff", "#ff8a3a", "#3ad0d0", "#e5484d")
BLOCK_SIZES = {"shot": (360, 240), "map": (360, 270), "note": (260, 180), "coords": (392, 200)}


def now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def lib_dir() -> str:
    import ssa_paths
    d = os.path.join(ssa_paths.data_dir(), "seed_library")
    for sub in ("", "images", "thumbs"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    return d


def image_path(block_id: str) -> str:
    return os.path.join(lib_dir(), "images", block_id + ".png")


def thumb_path(seed_id: str) -> str:
    return os.path.join(lib_dir(), "thumbs", seed_id + ".png")


class SeedStore(QObject):
    """The one store (store()): read once, saved after each change."""
    changed = Signal(str)                 # what changed: "seeds", "shelves" or a seed id

    def __init__(self):
        super().__init__()
        self.path = os.path.join(lib_dir(), "library.json")
        self.data = dict(version=1, shelves=[], seeds=[])
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                self.data.update(d)
        except (OSError, ValueError):
            pass
        self._save_t = QTimer(self)
        self._save_t.setSingleShot(True)
        self._save_t.setInterval(400)
        self._save_t.timeout.connect(self.save)

    # ── reading ──────────────────────────────────────────────────────────────
    @property
    def seeds(self) -> list:
        return self.data["seeds"]

    @property
    def shelves(self) -> list:
        return self.data["shelves"]

    def get(self, sid):
        return next((s for s in self.seeds if s["id"] == sid), None)

    def find(self, seed: int, version_name: str | None = None, large=None):
        for s in self.seeds:
            if int(s["seed"]) == int(seed) and (version_name is None or s.get("version") == version_name) \
                    and (large is None or bool(s.get("large")) == bool(large)):
                return s
        return None

    def shelf(self, shid):
        return next((h for h in self.shelves if h["id"] == shid), None)

    # ── writing ──────────────────────────────────────────────────────────────
    def touch(self, what="seeds", seed=None):
        if seed is not None:
            seed["modified"] = now_iso()
        self._save_t.start()
        self.changed.emit(what if seed is None else seed["id"])

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[seed library] could not save: {e}", flush=True)

    def add_seed(self, seed: int, version_name: str, large: bool, name: str = "", shelf=None):
        s = dict(id=new_id(), seed=str(int(seed)), version=version_name, large=bool(large), name=name,
                 tags=[], fav=False, notes="", shelves=[shelf] if shelf else [], board=[],
                 created=now_iso(), modified=now_iso())
        self.seeds.insert(0, s)
        self.touch("seeds")
        return s

    def remove_seed(self, sid):
        s = self.get(sid)
        if s is None:
            return
        trash = os.path.join(lib_dir(), "_trash")
        os.makedirs(trash, exist_ok=True)
        for b in s.get("board", []):
            p = image_path(b["id"])
            if os.path.isfile(p):
                shutil.move(p, os.path.join(trash, os.path.basename(p)))
        with open(os.path.join(trash, f"seed_{sid}.json"), "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        self.seeds.remove(s)
        self.touch("seeds")

    def add_shelf(self, name: str):
        used = {h.get("color") for h in self.shelves}
        col = next((c for c in SHELF_COLORS if c not in used), SHELF_COLORS[len(self.shelves) % len(SHELF_COLORS)])
        h = dict(id=new_id(), name=name, color=col, created=now_iso())
        self.shelves.append(h)
        self.touch("shelves")
        return h

    def remove_shelf(self, shid):
        self.data["shelves"] = [h for h in self.shelves if h["id"] != shid]
        for s in self.seeds:
            if shid in s.get("shelves", []):
                s["shelves"].remove(shid)
        self.touch("shelves")

    # ── boards ───────────────────────────────────────────────────────────────
    def add_block(self, s, kind, x, y, w=None, h=None, data=None):
        dw, dh = BLOCK_SIZES.get(kind, (300, 200))
        z = max([b.get("z", 0) for b in s["board"]] + [0]) + 1
        b = dict(id=new_id(), kind=kind, x=int(x), y=int(y), w=int(w or dw), h=int(h or dh),
                 z=z, data=dict(data or {}))
        s["board"].append(b)
        self.touch(seed=s)
        return b

    def remove_block(self, s, bid):
        b = next((b for b in s["board"] if b["id"] == bid), None)
        if b is None:
            return
        p = image_path(bid)
        if os.path.isfile(p):
            trash = os.path.join(lib_dir(), "_trash")
            os.makedirs(trash, exist_ok=True)
            shutil.move(p, os.path.join(trash, os.path.basename(p)))
        s["board"].remove(b)
        self.touch(seed=s)


_STORE = []


def store() -> SeedStore:
    if not _STORE:
        _STORE.append(SeedStore())
    return _STORE[0]
