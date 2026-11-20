"""
qtui/memory.py — how much memory the map may keep, from the RAM of the PC.

The map keeps what it has drawn (biome tiles, noise tiles, the biome ids of
each tile, the pictures of the structure icons, the structure plans) so that
coming back to a place or a zoom is instant. How much it keeps used to be
fixed (~800 MB at most): too much on an 8 GB laptop, very little on a 64 GB
PC. Now it is a budget:

  * Auto (default): a share of the RAM of this PC — 6 %, at least 384 MB and
    at most 4 GB (8 GB → ~490 MB, 16 GB → ~1 GB, 32 GB → ~2 GB, 64 GB → 4 GB);
  * or a size chosen in Settings → Performance ("Memory for the map").

apply() spreads the budget over the caches (MapEngine.MAX_TILES / MAX_NOISE /
MAX_IDS, map_view.ICON_CACHE_BYTES, the plan caches); the caches drop their
oldest pictures beyond it, they are never cleared by this module.
"""
from __future__ import annotations

import os
import sys

MB = 1 << 20
AUTO_SHARE = 0.06
AUTO_MIN_MB, AUTO_MAX_MB = 384, 4096
OLD_MB = 800                       # what the fixed sizes amounted to (the plan caches scale from it)

# the share of each cache, and what one entry weighs
TILE_KB = 256 + 16                 # a biome tile (256 x 256 RGB32) + its quick preview
NOISE_KB = 256 + 64                # a noise tile coloured + its raw values (uint8)
IDS_KB = 64                        # the biome ids of a tile (uint8)
SHARES = {"tiles": 0.42, "noise": 0.22, "ids": 0.06, "icons": 0.22, "plans": 0.08}

_total = None


def total_ram() -> int:
    """The physical RAM of this PC in bytes (8 GB when it cannot be read)."""
    global _total
    if _total is not None:
        return _total
    v = 0
    try:
        if sys.platform.startswith("win"):
            import ctypes

            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = _MS()
            ms.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                v = int(ms.ullTotalPhys)
        else:
            v = int(os.sysconf("SC_PAGE_SIZE")) * int(os.sysconf("SC_PHYS_PAGES"))
    except Exception:
        v = 0
    _total = v if v > 512 * MB else 8 << 30
    return _total


def auto_mb() -> int:
    return int(max(AUTO_MIN_MB, min(AUTO_MAX_MB, total_ram() / MB * AUTO_SHARE)))


def budget_mb() -> int:
    """The budget in MB: the setting, or Auto."""
    from . import prefs
    try:
        v = int(prefs.get("map_memory"))
    except Exception:
        v = 0
    return auto_mb() if v <= 0 else max(128, v)


def choices():
    """(value, text) for the Settings combo: Auto, then the sizes up to half the RAM."""
    half = total_ram() / MB / 2
    out = [(0, f"Auto (≈ {fmt(auto_mb())} on this PC)")]
    for mb in (256, 512, 1024, 2048, 4096, 8192, 16384):
        if mb <= half:
            out.append((mb, fmt(mb)))
    return out


def fmt(mb) -> str:
    return f"{mb / 1024:.1f} GB".replace(".0 GB", " GB") if mb >= 1024 else f"{int(mb)} MB"


def plan_count(n: int) -> int:
    """A plan cache that held n pictures: as many, scaled with the budget."""
    return int(max(n // 2, min(n * 6, n * budget_mb() / OLD_MB)))


def apply() -> int:
    """Spread the budget over the caches of the map (all the maps: the engine is
    shared). Called at launch and when the setting changes."""
    mb = budget_mb()
    try:
        from .map_engine import MapEngine
        MapEngine.MAX_TILES = max(200, int(mb * SHARES["tiles"] * 1024 / TILE_KB))
        MapEngine.MAX_NOISE = max(120, int(mb * SHARES["noise"] * 1024 / NOISE_KB))
        MapEngine.MAX_IDS = max(120, int(mb * SHARES["ids"] * 1024 / IDS_KB))
    except Exception:
        pass
    try:
        from . import map_view
        map_view.ICON_CACHE_BYTES = max(48 * MB, int(mb * SHARES["icons"] * MB))
    except Exception:
        pass
    return mb


def usage(engine, views=()) -> int:  # noqa: ARG001 (views: kept for callers)
    """What the map keeps now (bytes, estimated from the entries)."""
    n = 0
    try:
        n += len(engine.tiles) * 256 * 1024 + len(engine.previews) * 16 * 1024
        n += len(engine.noise_pix) * 256 * 1024 + len(engine.noise_raw) * 64 * 1024
        n += len(engine._ids) * IDS_KB * 1024
    except Exception:
        pass
    n += int(getattr(engine, "_iblk_bytes", 0))          # the structure pictures (shared)
    return n
