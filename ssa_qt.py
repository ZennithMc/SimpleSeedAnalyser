#!/usr/bin/env python3
"""
SimpleSeedAnalyser — new interface (Qt / PySide6).

    py ssa_qt.py

Requires:  py -m pip install PySide6
The classic interface (main.py) stays available during the migration.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _console_fallback():
    """A windowed .exe has no console: sys.stdout / sys.stderr are None and every
    traceback.print_exc() / sys.stderr.write() would raise. The GUI process
    writes them to console.log (the [native] load reports stay readable);
    worker processes discard them."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        import multiprocessing as _mp
        child = _mp.parent_process() is not None
    except Exception:
        child = False
    f = None
    if not child:
        try:
            import ssa_paths
            ssa_paths.migrate_legacy()
            f = open(os.path.join(ssa_paths.log_dir(), "console.log"), "w", encoding="utf-8", buffering=1)
        except Exception:
            f = None
    if f is None:
        f = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f


_console_fallback()


def _gc_in_gui_thread(app):
    """Run Python's cyclic garbage collector ONLY in the GUI thread.

    Automatic GC starts in whatever thread happens to allocate: a map worker
    (biomes, structures…) could then finalise cyclic garbage holding Qt
    wrappers created by the GUI thread while that thread was painting — an
    access violation 'Garbage-collecting' in a worker (crash.log 27/09, at
    launch: 2 s freeze, then the window vanished). Same fix as pyqtgraph's
    GarbageCollector: automatic GC off, periodic collections on a GUI timer.
    Plain reference counting (most memory) is unaffected."""
    import gc
    from PySide6.QtCore import QTimer
    gc.disable()
    tick = [0]

    def collect():
        tick[0] += 1
        try:
            gc.collect(2 if tick[0] % 120 == 0 else 1)   # full pass every ~60 s
        except Exception:
            pass

    t = QTimer(app)
    t.setInterval(500)
    t.timeout.connect(collect)
    t.start()
    app._ssa_gc_timer = t


def _crash_log():
    """Native crashes (a C DLL, Qt, CUDA) kill the process without a Python
    traceback: faulthandler writes the stack of EVERY thread to crash.log at
    that moment, so a one-off crash can be diagnosed afterwards. Uncaught
    Python exceptions (main or worker threads) and Qt fatal messages go to the
    same file. At the next launch, a new report is announced in the console."""
    import faulthandler
    import threading
    import time
    import traceback
    import ssa_paths
    try:
        ssa_paths.migrate_legacy()
        d = ssa_paths.log_dir()
        path = os.path.join(d, "crash.log")
        mark = path + ".seen"
        try:
            seen = int(open(mark).read().strip() or 0) if os.path.exists(mark) else 0
            if os.path.getsize(path) > seen:
                with open(path, encoding="utf-8", errors="replace") as _r:
                    _r.seek(seen)
                    _new = [ln.strip() for ln in _r.read().splitlines()]
                _n = sum(1 for ln in _new if ln.startswith("--- ") and ln.endswith(" ---"))
                _last = next((ln for ln in reversed(_new)
                              if ln and not ln.startswith(("---", "File ", "~", "^", "(same"))), "")
                _msg = (f"{_n or 'An'} error{'s' if _n != 1 else ''} recorded during the "
                        f"previous session (last: {_last[:120]}) — details in {path}")
                print("[SSA] " + _msg)
                os.environ["SSA_CRASH_NOTICE"] = _msg      # shown in the status bar too
        except OSError:
            pass
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:   # keep it small
            os.replace(path, path + ".old")
            if os.path.exists(mark):
                os.remove(mark)
        f = open(path, "a", encoding="utf-8", buffering=1)
        f.write(f"\n===== SSA started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(Python {sys.version.split()[0]}) =====\n")
        with open(mark, "w") as m:          # everything before this point was announced
            m.write(str(f.tell()))
        faulthandler.enable(file=f, all_threads=True)
    except Exception as e:
        print(f"[SSA] crash log unavailable: {e}")
        return

    repeats = {}

    def log_exc(kind, et, ev, tb):
        # the same error repeated on every tick filled the log (16 000 lines on
        # 27/09): each distinct error is written 3 times, then only counted
        try:
            last = traceback.extract_tb(tb)[-1] if tb else None
            key = (et.__name__, str(ev)[:200], last.filename if last else "", last.lineno if last else 0)
            n = repeats[key] = repeats.get(key, 0) + 1
            if n > 3:
                if n in (10, 100, 1000, 10000):
                    f.write(f"--- {time.strftime('%H:%M:%S')} (same error repeated {n} times: "
                            f"{et.__name__}: {str(ev)[:120]}) ---\n")
                return
            f.write(f"--- {time.strftime('%H:%M:%S')} {kind} ---\n")
            f.write("".join(traceback.format_exception(et, ev, tb)))
        except Exception:
            pass

    prev = sys.excepthook

    def hook(et, ev, tb):
        log_exc("uncaught exception", et, ev, tb)
        prev(et, ev, tb)
    sys.excepthook = hook

    prev_t = threading.excepthook

    def thook(a):
        log_exc(f"exception in thread {getattr(a.thread, 'name', '?')}", a.exc_type,
                a.exc_value, a.exc_traceback)
        prev_t(a)
    threading.excepthook = thook
    return f


def main():
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 is not installed.  Run:  py -m pip install PySide6")
        return 1
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    # Crisp on every screen: per-monitor DPI, no rounding of 125 % / 150 % scales.
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    log = _crash_log()
    if log is not None:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        def qt_msg(mode, ctx, msg):
            if mode in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                try:
                    log.write(f"--- Qt {mode.name}: {msg}\n")
                except Exception:
                    pass
            if mode != QtMsgType.QtDebugMsg and sys.stderr is not None:
                try:
                    sys.stderr.write(msg + "\n")
                except Exception:
                    pass
        qInstallMessageHandler(qt_msg)
    app = QApplication(sys.argv)
    app.setApplicationName("SimpleSeedAnalyser")
    _gc_in_gui_thread(app)
    from qtui import theme
    from qtui.main_window import MainWindow
    theme.apply(app)
    if not _cpu_has_avx2():
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "SimpleSeedAnalyser",
                             "This processor does not support AVX2 (CPUs from before ~2013–2015).\n"
                             "The search engine of this build requires it and would crash.")
        return 1
    win = MainWindow()
    win.show()
    return app.exec()


def _cpu_has_avx2():
    """libcubiomes is built with -mavx2: on an older CPU it would die with an
    illegal instruction (no message at all in the windowed .exe)."""
    # numpy runs CPUID at import and exposes the result (reliable on every
    # Windows version, unlike IsProcessorFeaturePresent(PF_AVX2)).
    try:
        try:
            from numpy._core._multiarray_umath import __cpu_features__ as f
        except ImportError:
            from numpy.core._multiarray_umath import __cpu_features__ as f
        return bool(f.get("AVX2", True))
    except Exception:
        return True


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if "--selftest" in sys.argv:              # installed-PC check (see selftest.py)
        import selftest
        sys.exit(selftest.main())
    sys.exit(main())
