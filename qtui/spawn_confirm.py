"""
qtui/spawn_confirm.py — Sessions: the real 1.16 spawn of each result (09/10).

  c = SpawnConfirm(parent); c.one.connect(slot)   # slot(seed, (text, tip, category))
  c.add(seeds)                                    # queued, checked by warm Minecraft workers
  c.reset()                                       # new search / cleared list

Texts: "checking…", "✓ lava · y 66", "✓ underground · y 70", "✓ structure · y 71",
"water · y 64", "surface · y 91", "normal", "?" (no answer). ✓ = a buggy spawn.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

CHECKING = ("checking…", "Waiting for Minecraft 1.16.1", "")
WTF = ("lava", "cave", "structure", "stuck")
# shown in a buggy-spawn search (the others are dropped): the buggy spawns and
# the normal spawns where the player lands under a roof at least half the time
KEEP = WTF + ("chance",)


class SpawnConfirm(QObject):
    one = Signal(object, object)          # seed (64-bit: object), (text, tip, category)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0
        self._lock = threading.Lock()
        self.waiting = 0

    def add(self, seeds):
        import spawn_check as sc
        g = self._gen
        ok, why = sc.status()
        for s in seeds:
            s = int(s)
            if not ok:
                self.one.emit(s, ("?", "Minecraft checker unavailable: " + str(why), ""))
                continue
            with self._lock:
                self.waiting += 1

            def cb(res, err, s=s, g=g):
                with self._lock:
                    if g == self._gen:
                        self.waiting = max(0, self.waiting - 1)
                if g != self._gen:
                    return
                if res is None:
                    self.one.emit(s, ("?", f"The Minecraft check failed: {err}", ""))
                    return
                cat, txt, tip = sc.classify(res)
                if cat in WTF:
                    txt = "✓ " + txt
                elif cat == "chance":
                    txt = "◐ " + txt
                self.one.emit(s, (txt, tip, cat))
            try:
                sc.check(s, cb)
            except Exception as e:                      # noqa: BLE001
                self.one.emit(s, ("?", f"Minecraft checker error: {e}", ""))

    def reset(self):
        self._gen += 1
        with self._lock:
            self.waiting = 0
        try:
            import spawn_check as sc
            if sc._POOL is not None:
                sc._POOL.clear()
        except Exception:
            pass
