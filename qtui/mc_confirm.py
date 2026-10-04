"""
qtui/mc_confirm.py — Sessions: every "Broken portal" result confirmed by the
real Minecraft generation (mc_checker), after the search finds it (03/10).

  c = McConfirm(parent); c.one.connect(slot)      # slot(seed, (text, tip))
  c.add(seeds, mc, params, large)                 # queued, checked in order
  c.reset()                                       # new search / cleared list

The seeds wait in a queue; one thread builds the checker requests in a separate
process (session_confirm.requests_for) and feeds the warm Minecraft workers,
never more than ~6 per worker ahead, so the search is never slowed.
Status: "checking…" → "✓ n" (n frames broken, natural chunk order), "✓ n /tp"
(only in the /tp order), "✗" (Minecraft keeps the frames), "?" (no answer).
"""
from __future__ import annotations

import queue
import threading
import time

from PySide6.QtCore import QObject, Signal

CHECKING = ("checking…", "Waiting for Minecraft 1.16.1", "")


def can_confirm(mc):
    return int(mc) in (19, 20)


class McConfirm(QObject):
    one = Signal(object, object)        # seed (64-bit: object), (text, tooltip, cause)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._q = queue.Queue()
        self._gen = 0
        self._lock = threading.Lock()
        self._th = None
        self._ex = None
        self.waiting = 0

    # ── public ──────────────────────────────────────────────────────────────
    def add(self, seeds, mc, params, large=False):
        g = self._gen
        with self._lock:
            self.waiting += len(seeds)
        for s in seeds:
            self._q.put((g, int(s), int(mc), dict(params), bool(large)))
        if self._th is None or not self._th.is_alive():
            self._th = threading.Thread(target=self._loop, name="ssa-mc-confirm", daemon=True)
            self._th.start()

    def reset(self):
        """Forget everything queued (the answers still on their way are ignored)."""
        self._gen += 1
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        with self._lock:
            self.waiting = 0
        # the few requests already handed to Minecraft finish and are ignored (the
        # pool is shared with the Stronghold page: never cleared from here)

    # ── worker ──────────────────────────────────────────────────────────────
    def _requests(self, seed, mc, params, large):
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool
        import session_confirm
        for _ in range(2):
            if self._ex is None:
                self._ex = ProcessPoolExecutor(max_workers=1)
            try:
                return self._ex.submit(session_confirm.requests_for, seed, mc, params,
                                       large).result(timeout=300)
            except BrokenProcessPool:
                self._ex = None
        return None

    def _done(self, g, seed, text, tip, cause=""):
        with self._lock:
            if g == self._gen:
                self.waiting = max(0, self.waiting - 1)
        if g == self._gen:
            self.one.emit(seed, (text, tip, cause))

    def _loop(self):
        try:
            import mc_checker
            ok, why = mc_checker.status()
        except Exception as e:                          # noqa: BLE001
            ok, why = False, str(e)
        while True:
            g, seed, mc, params, large = self._q.get()
            if g != self._gen:
                continue
            if not ok:
                self._done(g, seed, "?", "Minecraft checker unavailable: " + str(why))
                continue
            try:
                reqs = self._requests(seed, mc, params, large)
            except Exception as e:                      # noqa: BLE001
                self._done(g, seed, "?", f"Could not prepare the check: {e}")
                continue
            if not reqs:
                self._done(g, seed, "?", "No portal to check (the predicted cause was not "
                                         "found again).")
                continue
            try:
                pool = mc_checker.pool()
            except Exception as e:                      # noqa: BLE001
                self._done(g, seed, "?", f"Minecraft checker error: {e}")
                continue
            while g == self._gen and pool.pending() > 6 * pool.n + 4:
                time.sleep(0.2)
            if g != self._gen:
                continue
            self._submit(pool, g, seed, params, reqs)

    def _submit(self, pool, g, seed, params, reqs):
        need = max(1, int(params.get("min_frames", 1) or 1))
        st = dict(left=len(reqs), best=None, answered=0, causes=[])
        lock = threading.Lock()

        def make(rq):
            def cb(res, err):
                with lock:
                    st["left"] -= 1
                    if res is not None:
                        st["answered"] += 1
                        f, nat = int(res.get("forced", 0)), int(res.get("natural", -1))
                        if f >= need:
                            key = (nat > 0, f)
                            if st["best"] is None or key > st["best"][0]:
                                st["best"] = (key, rq["cause"], rq)
                    last = st["left"] == 0
                if not last:
                    return
                b = st["best"]
                if b is not None:
                    (natural, f), cause, rq_ = b
                    text = f"✓ {f}" if natural else f"✓ {f} /tp"
                    tip = (f"Minecraft 1.16.1: {f} frame(s) broken by a {cause}"
                           + ("." if natural else
                              " — only when the chunks load in the /tp order "
                              "(teleport straight to the portal)."))
                    self._done(g, seed, text, tip, cause)
                elif st["answered"]:
                    self._done(g, seed, "✗", "Minecraft 1.16.1 keeps the frames: not broken "
                                             f"(needed ≥ {need}).")
                else:
                    self._done(g, seed, "?", "The Minecraft check failed.")
            return cb

        for rq in reqs:
            pool.submit(seed, rq["frames"], rq["rox"], rq["roz"], make(rq),
                        natural="auto", sh=rq["sh"])
