"""
qtui/world.py — the world currently being explored (seed, version, world type).

A single WorldState object is shared by every screen: changing the seed in the
top bar, or loading a result from a search, updates the map, the analysis and
the searches together.
"""
from __future__ import annotations

import random

from PySide6.QtCore import QObject, Signal

import cubiomes_wrap as cw

VERSION_NAMES = list(cw.DROPDOWN_VERSIONS)
# (29/10) the game versions each entry stands for (tooltips of the version list)
VERSION_TIPS = {
    "Java 1.16.1": "1.16 → 1.16.1", "Java 1.16": "1.16.2 → 1.16.5", "Java 1.17": "1.17 → 1.17.1",
    "Java 1.18": "1.18 → 1.18.2", "Java 1.19.2": "1.19 → 1.19.2", "Java 1.19": "1.19.3 → 1.19.4",
    "Java 1.20": "1.20 → 1.20.6", "Java 1.21.1": "1.21 → 1.21.1", "Java 1.21.3": "1.21.2 → 1.21.3",
    "Java 1.21": "1.21.4 → 1.21.8", "Java 1.21.9": "1.21.9 → 1.21.11 (strongholds also in "
    "cherry groves)", "Java 26.1": "26.1.x", "Java 26.2": "26.2", "Java 26.3": "26.3",
}
# (31/10) what the version list shows: the game versions of each entry, in order
# (« Java 1.21 » — 1.21.4 → 1.21.8 — came after 1.21.3 and looked misplaced)
VERSION_LABELS = {
    "Java 1.16.1": "Java 1.16 – 1.16.1", "Java 1.16": "Java 1.16.2 – 1.16.5",
    "Java 1.17": "Java 1.17 – 1.17.1", "Java 1.18": "Java 1.18 – 1.18.2",
    "Java 1.19.2": "Java 1.19 – 1.19.2", "Java 1.19": "Java 1.19.3 – 1.19.4",
    "Java 1.20": "Java 1.20 – 1.20.6", "Java 1.21.1": "Java 1.21 – 1.21.1",
    "Java 1.21.3": "Java 1.21.2 – 1.21.3", "Java 1.21": "Java 1.21.4 – 1.21.8",
    "Java 1.21.9": "Java 1.21.9 – 1.21.11", "Java 26.1": "Java 26.1",
    "Java 26.2": "Java 26.2", "Java 26.3": "Java 26.3",
}


def version_label(name: str) -> str:
    return VERSION_LABELS.get(name, name)


# (31/10) every game version, by entry (the version menu: major → exact versions)
GAME_VERSIONS = {
    "Java 1.16.1": ["1.16", "1.16.1"], "Java 1.16": ["1.16.2", "1.16.3", "1.16.4", "1.16.5"],
    "Java 1.17": ["1.17", "1.17.1"], "Java 1.18": ["1.18", "1.18.1", "1.18.2"],
    "Java 1.19.2": ["1.19", "1.19.1", "1.19.2"], "Java 1.19": ["1.19.3", "1.19.4"],
    "Java 1.20": ["1.20", "1.20.1", "1.20.2", "1.20.3", "1.20.4", "1.20.5", "1.20.6"],
    "Java 1.21.1": ["1.21", "1.21.1"], "Java 1.21.3": ["1.21.2", "1.21.3"],
    "Java 1.21": ["1.21.4", "1.21.5", "1.21.6", "1.21.7", "1.21.8"],
    "Java 1.21.9": ["1.21.9", "1.21.10", "1.21.11"],
    "Java 26.1": ["26.1"], "Java 26.2": ["26.2"], "Java 26.3": ["26.3"],
}
EXACT_TO_NAME = {v: k for k, vs in GAME_VERSIONS.items() for v in vs}


def major_of(exact: str) -> str:
    """'1.21.4' -> '1.21', '26.2' -> '26'."""
    a = exact.split(".")
    return a[0] if a[0] != "1" else ".".join(a[:2])


_EXACT = [None]


def current_exact(name: str | None = None) -> str:
    """The exact game version chosen in the top bar (the last one of the entry
    when it does not match `name`)."""
    e = _EXACT[0]
    if e is None or (name is not None and EXACT_TO_NAME.get(e) != name):
        if name is None:
            return e or ""
        return GAME_VERSIONS.get(name, [""])[-1]
    return e


def set_current_exact(exact: str) -> None:
    _EXACT[0] = exact


DEFAULT_VERSION = "Java 1.21" if "Java 1.21" in VERSION_NAMES else VERSION_NAMES[-1]


def java_string_hash(s: str) -> int:
    """Minecraft turns a non-numeric seed into String.hashCode() (signed 32 bits)."""
    h = 0
    for ch in s:
        h = (31 * h + ord(ch)) & 0xFFFFFFFF
    return h - (1 << 32) if h >= (1 << 31) else h


def parse_seed(text: str) -> int | None:
    """Same rules as the game: an integer that fits in a signed 64-bit long is
    used as is; any other non-empty text is hashed like a Java string."""
    t = text.strip()
    if not t:
        return None
    try:
        v = int(t)
        if -(1 << 63) <= v < (1 << 63):
            return v
    except ValueError:
        pass
    return java_string_hash(t)


def random_seed() -> int:
    return random.getrandbits(64) - (1 << 63)


class WorldState(QObject):
    changed = Signal()                  # seed / version / world type changed

    def __init__(self):
        super().__init__()
        self.seed: int = random_seed()
        self.version_name: str = DEFAULT_VERSION
        self.large_biomes: bool = False

    @property
    def mc(self) -> int:
        return cw.MC_VERSIONS[self.version_name]

    @property
    def key(self):
        return (self.seed, self.mc, self.large_biomes)

    def set(self, seed=None, version_name=None, large_biomes=None):
        new = (self.seed if seed is None else int(seed),
               self.version_name if version_name is None else version_name,
               self.large_biomes if large_biomes is None else bool(large_biomes))
        if new == (self.seed, self.version_name, self.large_biomes):
            return
        self.seed, self.version_name, self.large_biomes = new
        cw.set_large_biomes(self.large_biomes)
        self.changed.emit()

    def structure_available(self, key: str) -> bool:
        if key == "dungeon":                         # (30/10) every version (1.17+ too)
            return 19 <= int(self.mc) <= 35
        if key == "lava_lake":                       # (10/10) 1.16 features only
            return int(self.mc) in (19, 20)
        need = cw.STRUCTURE_MIN_VERSION.get(key)
        return need is None or self.mc >= need
