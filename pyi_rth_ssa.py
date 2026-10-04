# PyInstaller runtime hook for SimpleSeedAnalyser (runs before ssa_qt.py, in the
# GUI process AND in every multiprocessing child).
#
# 1. Never let the dev auto-build run from the .exe (native_libs already checks
#    sys.frozen; this is belt and braces for terrain.py / cubiomes_wrap.py paths).
# 2. A windowed (console=False) build has sys.stdout / sys.stderr = None:
#    any sys.stderr.write(...) raises AttributeError (ssa_qt.py Qt message
#    handler), and every "[native] ..." diagnostic print is lost. Redirect them
#    to %LOCALAPPDATA%\SimpleSeedAnalyser\console.log in the GUI process, and to
#    os.devnull in worker processes (they would interleave in the same file).
import os
import sys

os.environ.setdefault("SSA_NO_AUTOBUILD", "1")

if sys.stdout is None or sys.stderr is None:
    _stream = None
    if "--multiprocessing-fork" not in sys.argv:
        try:
            if sys.platform == "win32":       # same places as ssa_paths.log_dir()
                _base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") \
                    or os.path.expanduser("~")
            else:
                _base = os.environ.get("XDG_STATE_HOME") or \
                    os.path.join(os.path.expanduser("~"), ".local", "state")
            _dir = os.path.join(_base, "SimpleSeedAnalyser")
            os.makedirs(_dir, exist_ok=True)
            _path = os.path.join(_dir, "console.log")
            try:
                if os.path.getsize(_path) > 5_000_000:
                    os.replace(_path, _path + ".old")
            except OSError:
                pass
            _stream = open(_path, "a", encoding="utf-8", errors="replace", buffering=1)
        except OSError:
            _stream = None
    if _stream is None:
        _stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = _stream
    if sys.stderr is None:
        sys.stderr = _stream
