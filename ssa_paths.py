"""
ssa_paths.py — where SSA keeps the user's things, per platform.

  data   sessions library, drawings, autosave, palettes, settings files
         Windows %APPDATA%\\SimpleSeedAnalyser
         Linux   $XDG_DATA_HOME/SimpleSeedAnalyser  (~/.local/share/…)
         macOS   ~/Library/Application Support/SimpleSeedAnalyser
  logs   console.log, crash.log, selftest.txt, debug traces
         Windows %LOCALAPPDATA%\\SimpleSeedAnalyser
         Linux   $XDG_STATE_HOME/SimpleSeedAnalyser (~/.local/state/…)
         macOS   ~/Library/Logs/SimpleSeedAnalyser

Before 29/09 Linux used a visible ~/SimpleSeedAnalyser folder (a beta tester:
"like a program creating folders on the Windows desktop"). migrate_legacy()
moves its content to the right places once.
"""
from __future__ import annotations

import os
import shutil
import sys

APP = "SimpleSeedAnalyser"
_WIN = sys.platform == "win32"
_MAC = sys.platform == "darwin"
_HOME = os.path.expanduser("~")


def _mk(d: str) -> str:
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def data_dir() -> str:
    if _WIN:
        base = os.environ.get("APPDATA") or _HOME
    elif _MAC:
        base = os.path.join(_HOME, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(_HOME, ".local", "share")
    return _mk(os.path.join(base, APP))


def log_dir() -> str:
    if _WIN:
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or _HOME
    elif _MAC:
        base = os.path.join(_HOME, "Library", "Logs")
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(_HOME, ".local", "state")
    return _mk(os.path.join(base, APP))


LOG_FILES = ("console.log", "console.log.old", "crash.log", "crash.log.seen",
             "selftest.txt", "corrupt_debug.txt")


def migrate_legacy() -> str | None:
    """Linux / macOS: move ~/SimpleSeedAnalyser (old location) into data_dir()
    and log_dir(). Nothing is overwritten; the old folder is removed only when
    empty. Returns a short note when something was moved."""
    if _WIN:
        return None
    old = os.path.join(_HOME, APP)
    if not os.path.isdir(old) or os.path.realpath(old) == os.path.realpath(data_dir()):
        return None
    moved = 0
    for name in os.listdir(old):
        src = os.path.join(old, name)
        dst_dir = log_dir() if name in LOG_FILES else data_dir()
        dst = os.path.join(dst_dir, name)
        if os.path.exists(dst):
            if os.path.isdir(src) and os.path.isdir(dst):      # merge folders (library/)
                for root, _dirs, files in os.walk(src):
                    rel = os.path.relpath(root, src)
                    for f in files:
                        t = os.path.join(dst, rel, f)
                        if not os.path.exists(t):
                            _mk(os.path.dirname(t))
                            try:
                                shutil.move(os.path.join(root, f), t); moved += 1
                            except OSError:
                                pass
            continue
        try:
            shutil.move(src, dst); moved += 1
        except OSError:
            pass
    # remove what is left if it is only empty folders
    for root, dirs, files in os.walk(old, topdown=False):
        if not files:
            try:
                os.rmdir(root)
            except OSError:
                pass
    if moved:
        return f"Your SSA files moved from ~/{APP} to {data_dir()}"
    return None
