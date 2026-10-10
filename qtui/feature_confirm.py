"""
qtui/feature_confirm.py — Sessions: are a result's 1.16 dungeons / lava lakes
real? (10/10)

(10/10 bis) The engine now rebuilds the exact 1.16.1 terrain: the features it
counts really generate. A result is kept at once when each top-level dungeon /
lava lake condition has its count of real ones; only features marked unsure
(near a village / outpost, not modelled) — or every one with an older library —
are sent to the real Minecraft 1.16.1 (the warm SpawnWorker processes).

  c = FeatureConfirm(parent); c.one.connect(slot)  # slot(seed, (text, tip, cat), feats)
  c.add([(seed, conds, mc)])
  c.reset()
cat: "feat_ok" (kept) / "feat_no" (dropped) / "?" (no answer).
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

CHECKING = ("checking…", "Waiting for Minecraft 1.16.1", "")
MAX_PER_COND = 48           # features checked per condition (nearest the area centre first)


class FeatureConfirm(QObject):
    one = Signal(object, object, object)     # seed, (text, tip, cat), [Feat confirmed]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gen = 0
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self._gen += 1

    def add(self, items):
        """(10/10 bis) computed on a worker thread (the exact terrain takes a
        moment per seed); the results come back through the `one` signal."""
        items = list(items)
        if not items:
            return
        if not hasattr(self, "_pool"):
            from concurrent.futures import ThreadPoolExecutor
            self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="feat116")
        g = self._gen
        for i in range(0, len(items), 8):
            self._pool.submit(self._add_safe, items[i:i + 8], g)

    def _add_safe(self, items, g):
        try:
            self._add(items, g)
        except Exception as e:                          # noqa: BLE001
            for seed, _c, _m in items:
                self.one.emit(int(seed), ("?", f"feature error: {e}", "?"), [])

    def _add(self, items, g):
        import feature116 as f116
        import spawn_check as sc
        ok, why = sc.status()
        for seed, conds, mc in items:
            if g != self._gen:
                return
            seed = int(seed)
            try:
                targets = f116.confirm_targets(conds, seed, mc)
            except Exception as e:                      # noqa: BLE001
                self.one.emit(seed, ("?", f"feature error: {e}", "?"), [])
                continue
            if not targets:
                self.one.emit(seed, ("◌ not checked", "Relative / excluded dungeon or lava lake "
                                     "conditions are not checked with Minecraft.", "feat_ok"), [])
                continue
            # (10/10 bis) exact terrain: the real features count at once; only
            # the unsure ones (near a village / an older library) go to Minecraft
            sure_ok, lines0 = True, []
            for c, need, fs in targets:
                real = [f for f in fs if f.real]
                name = "dungeon" if c.get("type") == "dungeon" else "lava lake"
                ys = ", ".join(f"Y {f.y}" for f in real[:4])
                lines0.append(f"{name}: {len(real)} real (exact terrain, needs {need})" + (f" · {ys}" if ys else ""))
                if len(real) < need:
                    sure_ok = False
            if sure_ok:
                good = [f for _c, _n, fs in targets for f in fs if f.real]
                f0 = good[0] if good else None
                txt = "✓ " + (f"{f0.name.lower()} · Y {f0.y}" if f0 else "real")
                self.one.emit(seed, (txt, "\n".join(lines0), "feat_ok"), good)
                continue
            if all(not f116.uncertain(fs) for _c, _n, fs in targets):
                self.one.emit(seed, ("✗ not generated", "\n".join(lines0), "feat_no"), [])
                continue
            if not ok:
                self.one.emit(seed, ("?", "Minecraft checker unavailable: " + str(why), "?"), [])
                continue
            plan, feats = [], []
            for c, need, fs in targets:
                cx = (int(c.get("x1", 0)) + int(c.get("x2", 0))) / 2
                cz = (int(c.get("z1", 0)) + int(c.get("z2", 0))) / 2
                real = [f for f in fs if f.real]
                fs = sorted(f116.uncertain(fs), key=lambda f: (f.x - cx) ** 2 + (f.z - cz) ** 2)[:MAX_PER_COND]
                plan.append((c, max(0, need - len(real)), len(feats), len(fs), real))
                feats += fs
            if not feats:
                self.one.emit(seed, ("✗ none", "No feature left to check.", "feat_no"), [])
                continue

            def cb(res, err, seed=seed, plan=plan, g=g, f116=f116):
                if g != self._gen:
                    return
                if res is None:
                    self.one.emit(seed, ("?", f"The Minecraft check failed: {err}", "?"), [])
                    return
                good, lines, keep = [], [], True
                for c, need, i0, n, real0 in plan:
                    part = res[i0:i0 + n]
                    real = real0 + [f for f, ok, _d in part if ok]
                    unk = [f for f, ok, _d in part if ok is None]       # surface lakes
                    got = len(real) + len(unk)
                    name = "dungeon" if c.get("type") == "dungeon" else "lava lake"
                    if got < need:
                        keep = False
                    good += real + unk
                    ys = ", ".join(f"Y {f116.real_y(seed, f) if f.kind == 1 else f.y}"
                                   for f in (real + unk)[:4])
                    lines.append(f"{name}: {len(real)} real / {n} checked (needs {need})"
                                 + (f" · {ys}" if ys else ""))
                if keep:
                    f0 = good[0] if good else None
                    y0 = (f116.real_y(seed, f0) if f0 is not None and f0.kind == 1 else None)
                    txt = "✓ " + (f"{f0.name.lower()} · Y {y0 if y0 is not None else f0.y}" if f0
                                  else "real in Minecraft")
                    self.one.emit(seed, (txt, "\n".join(lines), "feat_ok"), good)
                else:
                    self.one.emit(seed, ("✗ not generated", "\n".join(lines), "feat_no"), [])
            f116.check(seed, feats, cb)
