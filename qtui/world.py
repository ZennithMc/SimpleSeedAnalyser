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
        if key in ("dungeon", "lava_lake"):          # (10/10) 1.16 features only
            return int(self.mc) in (19, 20)
        need = cw.STRUCTURE_MIN_VERSION.get(key)
        return need is None or self.mc >= need
