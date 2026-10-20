"""
qtui/prefs.py — user preferences of the Settings window (17/10, extended 17/10 bis).

One place for every setting of the Settings window, stored with QSettings
under "prefs/". Values are cached in memory (the map reads some of them on
every wheel step) and the cache is refreshed by set() / reset().

  * questions SSA asks again and again — "ask" keeps asking (the behaviour
    before this window existed);
  * general, map, performance and appearance options; a few only apply at the
    next launch (RESTART).
"""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings

# ── the recurring questions: key -> (default, [(value, label)], tooltip) ────────
CHOICES = {
    "resume_conditions_changed": (
        "ask",
        [("ask", "Ask every time"), ("recheck", "Resume + re-check the results"),
         ("keep", "Resume, keep the results"), ("restart", "Restart from the start seed")],
        "When you resume a paused search after changing its conditions."),
    "resume_space_changed": (
        "ask",
        [("ask", "Ask every time"), ("new", "Start a new search"),
         ("resume", "Resume the paused search")],
        "When you resume after changing the start seed, the mode, the seed list,\n"
        "the version or Large Biomes."),
    "safety_first": (
        "100000",
        [("100000", "Pause at 100 000 results and ask"),
         ("1000000", "Pause at 1 000 000 results and ask"),
         ("10000000", "Only at 10 000 000 (the maximum)")],
        "A search that finds that many results pauses itself: the conditions are probably\n"
        "too broad, and millions of results make SSA slow."),
    "topy_family": (
        "ask",
        [("ask", "Ask every time"), ("switch", "Always switch to 48-bit family"),
         ("keep", "Never (keep the mode)")],
        "A Top Y session runs much faster in 48-bit family mode."),
    "notify_done": (
        "flash",
        [("off", "Nothing"), ("flash", "Flash the taskbar"),
         ("sound", "Flash the taskbar + a sound")],
        "When a search ends by itself (finished, or paused at its limit) while SSA is\n"
        "in the background."),
    "default_threads": (
        "remember",
        [("remember", "Remember the last value"), ("all", "All the cores"),
         ("most", "All the cores but one"), ("half", "Half of the cores")],
        "Threads of the Sessions, Stronghold and Draw searches at each launch."),
}

LABELS = {
    "resume_conditions_changed": "Conditions changed during a pause",
    "resume_space_changed": "Start / mode / version changed during a pause",
    "safety_first": "Many results",
    "topy_family": "Top Y session not in 48-bit family",
    "notify_done": "When a search ends",
    "default_threads": "Threads at launch",
}

# ── other settings: key -> default (bool / float / int / str) ──────────────────
VALUES = {
    "restore_state": True,          # come back as SSA was closed (ui_state)
    "inertia": True,                # the map keeps sliding after a drag
    "zoom_speed": 1.0,              # wheel zoom step factor
    "struct_max_bpp": 48.0,         # structures shown up to this zoom (blocks / pixel)
    "use_gpu": True,                # RESTART: NVIDIA GPU libraries
    "ui_scale": 100,                # RESTART: interface scale in %
    "java_home": "",                # JDK 17 for the Minecraft check ("" = automatic)
}
RESTART = {"use_gpu", "ui_scale"}

_CACHE: dict = {}


def _qs():
    return QSettings("SSA", "SimpleSeedAnalyser")


def _default(key):
    return CHOICES[key][0] if key in CHOICES else VALUES[key]


def get(key: str):
    if key in _CACHE:
        return _CACHE[key]
    default = _default(key)
    try:
        v = _qs().value(f"prefs/{key}", default)
    except Exception:
        v = default
    if key in CHOICES:
        v = str(v)
        if v not in {c for c, _l in CHOICES[key][1]}:
            v = default
    elif isinstance(default, bool):
        v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
    elif isinstance(default, float):
        try:
            v = float(v)
        except (TypeError, ValueError):
            v = default
    elif isinstance(default, int):
        try:
            v = int(float(v))
        except (TypeError, ValueError):
            v = default
    else:
        v = "" if v is None else str(v)
    _CACHE[key] = v
    return v


def set(key: str, value) -> None:            # noqa: A001 (module API)
    _qs().setValue(f"prefs/{key}", value)
    _CACHE.pop(key, None)


def reset() -> None:
    qs = _qs()
    for k in list(CHOICES) + list(VALUES):
        qs.remove(f"prefs/{k}")
    _CACHE.clear()


def threads_at_launch(current: int) -> int:
    """The threads value a page starts with (default_threads)."""
    n = os.cpu_count() or 4
    mode = get("default_threads")
    if mode == "all":
        return n
    if mode == "most":
        return max(1, n - 1)
    if mode == "half":
        return max(1, n // 2)
    return current


def apply_startup_env() -> None:
    """Before the QApplication exists (ssa_qt.main): interface scale and the
    GPU switch (worker processes inherit the environment)."""
    try:
        sc = int(get("ui_scale"))
        if sc != 100 and 50 <= sc <= 300 and not os.environ.get("QT_SCALE_FACTOR"):
            os.environ["QT_SCALE_FACTOR"] = f"{sc / 100:.2f}"
        if not get("use_gpu"):
            os.environ["SSA_NO_GPU"] = "1"
    except Exception:
        pass


def notify_done(window) -> None:
    """A search ended by itself: flash the taskbar (+ a sound)."""
    try:
        mode = get("notify_done")
        if mode == "off" or window is None:
            return
        from PySide6.QtWidgets import QApplication
        QApplication.alert(window, 0)
        if mode == "sound":
            QApplication.beep()
    except Exception:
        pass
