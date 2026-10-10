"""
qtui/search_runner.py — runs a custom session in a background thread and feeds
the UI (progress, speed, engine really used, every result, errors).

* results are never capped: they are buffered by the worker and handed to the
  GUI thread in batches (``resultsReady``) — the table model is virtual;
* Pause = stop at a safe point + remember where to restart. With the C engine
  the restart point is exact (first seed not fully tested, see
  ssa_engine_resume_point); results found twice are de-duplicated. Engines
  without a resume point (GPU pipelines, seed lists) cannot be paused: the
  button is disabled as soon as such an engine is reported;
* the engine label says what actually runs ("C engine · 12 threads",
  "GPU structures + C validation", "Python (slow!)"…).
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

MODE_LABEL = {0: "Incremental", 1: "48-bit family", 2: "48-bit family"}
FAMILY = 65536                     # seeds per 48-bit family
# Safety: a too-broad session (e.g. one easy condition) finds millions of seeds;
# every one is kept in memory, shown in the table and saved in the autosave →
# the app got slow and crashed (beta tester, 28/09). The search PAUSES at this
# many results unless the stop rule itself is a result count; the user can
# continue with a 10× higher limit, up to SAFETY_MAX.
SAFETY_FIRST = 100_000
SAFETY_MAX = 10_000_000
M48 = (1 << 48) - 1
U64 = 1 << 64
EFF = {0: "inc", 1: "fam48", 2: "blocks"}   # engine's effective mode codes


def rule_text(rule):
    kind, val = rule
    if kind == "seeds":
        return f"{val:,} seeds".replace(",", " ")
    if kind == "results":
        return f"{val:,} results".replace(",", " ")
    if kind == "time":
        h, m = divmod(int(val) // 60, 60)
        return (f"{h} h {m:02d}" if h else f"{m} min") if val >= 60 else f"{int(val)} s"
    return "never"


class SearchRunner(QObject):
    resultsReady = Signal(list)            # [(seed, details), …] new unique results
    # object, not dict: a dict is converted to a QVariantMap, and a 48-bit family
    # search counts up to 2^64 seeds (> int64) -> OverflowError on every tick, no
    # live progress at all (seen on a 4-structure GPU session, 27/09).
    progress = Signal(object)              # done, total, sps, eta, found, elapsed
    engine = Signal(str, str)              # label, tooltip
    stateChanged = Signal(str)             # idle / running / pausing / paused / stopping / done / error
    message = Signal(str)                  # short status text
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._buf = []
        self._seen = set()
        self.found = 0
        self.safety_limit = SAFETY_FIRST
        self._timer = QTimer(self, interval=80, timeout=self._pump)
        self._rate = []                    # (t, done) samples for the speed
        self.can_pause = True
        self.resume_point = None
        self.params = None
        self.rule = ("never", 0)
        self.auto_paused = None
        self.plan = None                   # engine chosen by GPU: Auto / the Engine window
        self._planning = None
        self.eff_mode = None               # mode the C engine really ran (inc / blocks)
        self.profile_base = None           # per-condition profile of earlier runs (pauses)
        self._prof_live = None
        self._expect_eff = None            # resume: the engine must keep that mode
        self._mode_err = None

    # ── public API ──────────────────────────────────────────────────────────
    def _count_for(self, rule, mode):
        """Engine count (iteration units: seeds in Incremental, 48-bit families
        in 48-bit family) for a 'seeds searched' limit, 0 = unlimited."""
        kind, val = rule
        if kind != "seeds" or val <= 0:
            return 0
        return int(val) if mode == "inc" else -(-int(val) // FAMILY)

    def start(self, conditions, mc, start, threads, mode="inc", seed_list=None,
              rule=("never", 0), _resume=False, _count=None, job=None, engine_label=None,
              gpu="as_set", plan=None, _engine_mode=None, options=None):
        """conditions: engine-format list (already exported, deep-copied).
        options: "48-bit family" options (hide_sisters, struct_only — 08/10).
        rule: ("never"|"seeds"|"results"|"time", value) — when to stop.
        A results / time limit PAUSES the search (it can be resumed, e.g. with
        a higher limit); a seeds limit ends it."""
        if self.state in ("running", "pausing", "stopping"):
            return
        if not _resume:
            self._seen.clear()
            self.found = 0
            self.safety_limit = SAFETY_FIRST
            self.base_start = int(start)
            self._elapsed_before = 0.0
            self._done_before = 0
            self.eff_mode = None
            self._expect_eff = None
            self.profile_base = None
        self._prof_live = None
        count = self._count_for(rule, mode) if _count is None else _count
        if not _resume:
            self.base_count = count
        self.rule = rule
        self.auto_paused = None
        self.params = dict(conditions=conditions, mc=mc, threads=threads, mode=mode,
                           seed_list=seed_list, job=job, engine_label=engine_label,
                           gpu=gpu, plan=plan, engine_mode=_engine_mode or mode,
                           options=dict(options or {}))
        self._mode_err = None
        self.plan = None
        self._planning = None
        self._stop.clear()
        self._err = None
        self._done_flag = False
        self._limit_hit = False
        self._safety_hit = False
        self._overflow = []
        while self.found >= self.safety_limit and self.safety_limit < SAFETY_MAX:
            self.safety_limit *= 10                 # resumed past the limit (restored session)
        self._last = (0, 0)
        self._rate = []
        self._engine_modes = []
        self._gpu_note = ""
        self.can_pause = seed_list is None and job is None
        self.resume_point = None
        self._run_start, self._run_count = int(start), int(count) if seed_list is None else 0
        self._t0 = time.perf_counter()
        self._set_state("running")
        self._engine_label_initial(threads)
        self._thread = threading.Thread(target=self._work, name="ssa-search", daemon=True)
        self._thread.start()
        self._timer.start()

    def pause(self, reason=None):
        if self.state == "running" and self.can_pause:
            self.auto_paused = reason
            self._set_state("pausing")
            self._stop.set()

    def _safety(self):
        """Result count at which the search pauses by itself (safety)."""
        if self.rule[0] == "results" and self.rule[1] > 0:
            return max(self.rule[1], SAFETY_MAX)        # an explicit result count wins
        return self.safety_limit

    def raise_safety(self):
        """×10 (up to SAFETY_MAX). Returns the new limit, or None if at the max."""
        if self.safety_limit >= SAFETY_MAX:
            return None
        self.safety_limit = min(SAFETY_MAX, self.safety_limit * 10)
        return self.safety_limit

    def limit_reached(self, rule=None):
        """Is the (new) limit already reached by what was done before?"""
        kind, val = rule or self.rule
        if kind == "results":
            return val > 0 and self.found >= val
        if kind == "time":
            return val > 0 and self._elapsed_before >= val
        if kind == "seeds":
            return val > 0 and self._done_before >= val
        return False

    def resume(self, conditions=None, threads=None, rule=None, gpu=None, plan=None):
        """Continue exactly where the pause stopped, optionally with other
        conditions, thread count or stop rule (the mode and the start seed are
        fixed for the whole search). Returns an error text or None."""
        if self.state != "paused" or self.resume_point is None:
            return "Nothing to resume."
        p = self.params
        rule = rule or self.rule
        if self.limit_reached(rule):
            return (f"The stop limit ({rule_text(rule)}) is already reached — "
                    f"raise it (or choose Never) to continue.")
        # The resume point is in the units of the mode the engine REALLY ran:
        # a 64-bit seed when "48-bit family" was adapted to Incremental (no
        # structure), a 48-bit low in Blocks. The restart forces that mode
        # (before: fam48 was requested again and the counts mixed both units).
        eff = self.eff_mode or p["mode"]
        eng_mode = "inc" if eff == "inc" else p["mode"]
        count = 0
        if rule[0] == "seeds" and rule[1] > 0:
            if eff == "inc":
                count = int(rule[1]) - (self.resume_point - self.base_start) % U64
            else:
                count = ((self.base_start & M48) + self._count_for(rule, "fam48")
                         - self.resume_point)
            if count <= 0:
                return "The seed limit is already reached — raise it to continue."
        self._expect_eff = eff if eff in ("inc", "blocks") else None
        self.start(conditions if conditions is not None else p["conditions"], p["mc"],
                   self.resume_point, threads or p["threads"], p["mode"], None, rule,
                   _resume=True, _count=count, _engine_mode=eng_mode,
                   gpu=gpu if gpu is not None else p.get("gpu", "as_set"),
                   plan=plan if gpu is not None else p.get("plan"),
                   options=p.get("options"))
        return None

    # ── session files: keep / restore a paused search ──────────────────────
    def export_state(self):
        """What a saved session needs to continue this search later (None when
        there is nothing to continue)."""
        if self.params is None:
            return None
        st = dict(state=self.state, base_start=int(getattr(self, "base_start", 0)),
                  mode=self.params.get("mode", "inc"), eff_mode=self.eff_mode,
                  done=int(self._done_before), elapsed=float(self._elapsed_before),
                  found=int(self.found), rule=list(self.rule),
                  options=dict(self.params.get("options") or {}))
        if self.state == "paused" and self.resume_point is not None:
            st["resume_point"] = int(self.resume_point)
        return st

    def restore_paused(self, conditions, mc, threads, st, seen):
        """Put the runner back in the 'paused' state of a saved search: Resume
        then continues exactly from its resume point."""
        if self.state in ("running", "pausing", "stopping"):
            return False
        mode = st.get("mode", "inc")
        self.params = dict(conditions=conditions, mc=mc, threads=threads, mode=mode,
                           seed_list=None, job=None, engine_label=None, gpu="as_set", plan=None,
                           engine_mode=mode, options=dict(st.get("options") or {}))
        self.base_start = int(st.get("base_start", 0))
        self.resume_point = int(st["resume_point"])
        self.eff_mode = st.get("eff_mode") or ("inc" if mode == "inc" else None)
        self._expect_eff = None
        self._done_before = int(st.get("done", 0))
        self._elapsed_before = float(st.get("elapsed", 0.0))
        self.rule = tuple(st.get("rule") or ("never", 0))
        self._prof_live = None
        with self._lock:
            self._seen = set(seen)
            self.found = len(self._seen)
        self.can_pause = True
        self.auto_paused = None
        self._set_state("paused")
        self.message.emit(f"Paused (restored)  ·  {self.summary()}  ·  resume from {self.resume_point}")
        return True

    def stop(self):
        if self.state in ("running", "pausing"):
            self._set_state("stopping")
            self._stop.set()
        elif self.state == "paused":
            self._set_state("done")
            self.message.emit(f"Stopped  ·  {self.summary()}")

    # ── worker ──────────────────────────────────────────────────────────────
    def _work(self):
        import custom_search as cs
        p = self.params

        def on_match(seed, details=""):
            s = int(seed)
            # one row per seed; stronghold jobs can report several strongholds of
            # the same seed (all-128 scans) → keyed by position as well
            key = (s, details.get("x"), details.get("z")) if isinstance(details, dict) else s
            with self._lock:
                if key in self._seen:
                    return
                target = self.rule[1] if (self.rule[0] == "results" and self.rule[1] > 0) \
                    else self._safety()
                if self.found >= target:
                    # the engine takes a moment to stop (seeds in flight on every
                    # thread): a limit of 5 gave 625 results, 2 000 gave 174 000.
                    # Keep the extra matches aside; at the pause, those BEFORE
                    # the resume point are added (they would never be found
                    # again), the others are found again after Resume.
                    self._overflow.append((key, s, details or ""))
                    return
                self._seen.add(key)
                self._buf.append((s, details or ""))
                self.found += 1
                if self.rule[0] == "results" and self.rule[1] > 0 and self.found >= self.rule[1]:
                    self._limit_hit = True
                    self._stop.set()
                elif self.found >= self._safety():
                    self._limit_hit = True
                    self._safety_hit = True
                    self._stop.set()

        def on_progress(done, total):
            self._last = (int(done), int(total))

        def on_mode(m):
            self._engine_modes.append(m)

        def on_resume(pt):
            self.resume_point = int(pt)

        def on_profile(rows, total):
            self._prof_live = (rows, total)

        run_conds = p["conditions"]
        if p.get("job") is None and p["seed_list"] is None and p.get("gpu") in ("auto", "cpu", "plan"):
            import gpu_planner as gp
            if p["gpu"] == "cpu":
                run_conds = gp.strip_gpu(run_conds)
            elif p["gpu"] == "plan" and p.get("plan") is not None:
                self.plan = p["plan"]                   # analysed in the Engine window
                run_conds = p["plan"].conditions
            else:
                # GPU: Auto — measure CPU vs the GPU pre-filters, keep the fastest
                self.can_pause = False                  # nothing to resume while calibrating
                self._planning = "GPU auto: preparing…"
                try:
                    plan = gp.plan_gpu(run_conds, p["mc"], self._run_start, p["threads"],
                                       p["engine_mode"], status=self._set_planning, stop=self._stop)
                    self.plan = plan
                    run_conds = plan.conditions
                    self.message.emit("Engine: " + plan.detail)   # never silent
                except Exception as e:                  # never block the search
                    import traceback
                    traceback.print_exc()
                    self.plan = None
                    self._gpu_note = f"GPU auto failed ({type(e).__name__}: {e}) — CPU used."
                    run_conds = gp.strip_gpu(run_conds)
                self._planning = None
                self.can_pause = True
                self._rate = []
        try:
            if p.get("job") is not None:
                p["job"](self._run_start, self._run_count, p["threads"], self._stop,
                         on_match, on_progress, on_mode)
            else:
                # result limit / safety cap applied inside the C engine (p96):
                # it stops right after that many NEW matches instead of running
                # a whole ring buffer ahead of the Python drain
                target = self.rule[1] if (self.rule[0] == "results" and self.rule[1] > 0) \
                    else self._safety()
                cap = max(1, target - self.found) + self._refind_count()
                cs.evaluate_session(run_conds, self._run_start, self._run_count, p["mc"],
                                    p["threads"], self._stop, on_match, on_progress,
                                    search_mode=p["engine_mode"], on_mode=on_mode,
                                    seed_list=p["seed_list"], on_resume=on_resume,
                                    on_profile=on_profile, max_found=cap,
                                    options=p.get("options"))
        except Exception as e:                                 # reported, never swallowed
            import traceback
            traceback.print_exc()
            self._err = f"{type(e).__name__}: {e}"
        self._done_flag = True

    # ── GUI thread ──────────────────────────────────────────────────────────
    def _engine_label_initial(self, threads):
        self.engine.emit("Starting…", "")

    def _set_planning(self, text):
        self._planning = text

    def _engine_label(self):
        if self._planning:
            return (self._planning, "Measuring which engine is fastest for this session "
                                    "(a few seconds, once per session and settings).")
        lab, tip = self._engine_label_base()
        plan = getattr(self, "plan", None)
        if plan is not None:
            from .sessions_page import fmt_rate
            lines = [plan.detail]
            if plan.report:
                lines += ["", "Measured:"]
            notes = getattr(plan, "notes", {}) or {}
            for name, rate in plan.report:
                lines.append(f"  • {name}: " + (fmt_rate(rate) if rate else
                                                 "no speed — " + notes.get(name, "GPU not engaged")))
            lab = plan.label + ("" if "GPU" in plan.label else f" · {lab}")
            tip = "\n".join(lines) + ("\n\n" + tip if tip else "")
        return lab, tip

    def _engine_label_base(self):
        modes = self._engine_modes
        p = self.params
        if p.get("engine_label"):
            lab = p["engine_label"]
            notes = [str(m[1]) for m in modes if isinstance(m, tuple) and m and m[0] == "note"]
            return (lab + (" · " + notes[-1] if notes else ""), "")
        gpu_status = [m for m in modes if isinstance(m, tuple) and m and m[0] == "gpu_status"]
        ints = [m for m in modes if isinstance(m, int)]
        if gpu_status:
            self._gpu_note = str(gpu_status[-1][1])
        base_pause = p["seed_list"] is None and p.get("job") is None
        if ints and ints[-1] == 3:          # the last mode: 3 then 0/1/2 = GPU fell back
            # 08/10: the hybrid GPU pipelines give a resume point (Incremental)
            res = any(isinstance(m, tuple) and m and m[0] == "resumable" for m in modes)
            self.can_pause = base_pause and res
            return ("GPU (CUDA) + C validation",
                    "The GPU pre-filters the seeds; every candidate is fully checked by the "
                    "C engine. Results are exact."
                    + ("" if res else "\nThis GPU pipeline cannot be paused (Stop only)."))
        if ints and not self._planning:
            self.can_pause = base_pause     # a GPU path that fell back to the C engine
        if ints and p["seed_list"] is not None:
            if ints[-1] == 1:
                return ("Seed list · 48-bit families · C engine",
                        "Every listed seed stands for its 48-bit family: its 65 536 sister "
                        "seeds (same lower 48 bits) are all tested. Families whose "
                        "structure positions fail are skipped at once.")
            return "Seed list · C engine", "Tests exactly the seeds of the list."
        if ints:
            m = ints[-1]
            lab = f"C engine · {p['threads']} threads · {MODE_LABEL.get(m, '?')}"
            tip = "Native multithreaded engine."
            want = {"inc": 0, "fam48": 1, "blocks": 2}.get(p["mode"], 0)
            if MODE_LABEL.get(m) != MODE_LABEL.get(want):
                tip += (f"\nRequested mode {MODE_LABEL.get(want)} was adapted to "
                        f"{MODE_LABEL.get(m)} for this session (results are exact).")
            if self._gpu_note:
                lab += " · GPU unavailable"
                tip += "\n" + self._gpu_note
            return lab, tip
        if p["seed_list"] is not None:
            return "Seed list · C engine", "Tests exactly the seeds of the list."
        return "Running…", ""

    def _eff_now(self):
        ints = [m for m in self._engine_modes if isinstance(m, int) and m in EFF]
        return EFF[ints[-1]] if ints else None

    def _pump(self):
        if self._expect_eff and self._eff_now():
            eff = self._eff_now()
            if eff != self._expect_eff:
                # edited conditions switched Incremental <-> Blocks: the resume
                # point (seed vs 48-bit low) means something else in that mode
                self._mode_err = (f"The edited conditions change the search mode "
                                  f"({MODE_LABEL[{'inc': 0, 'blocks': 2}[self._expect_eff]]} → "
                                  f"{MODE_LABEL.get({'inc': 0, 'blocks': 2}.get(eff, 1))}), so the paused "
                                  f"search cannot continue from its resume point. Start a new search.")
                self._stop.set()
            self._expect_eff = None
        with self._lock:
            batch, self._buf = self._buf, []
        if batch:
            self.resultsReady.emit(batch)
        lab, tip = self._engine_label()
        self.engine.emit(lab, tip)
        done, total = self._last
        mult = self._unit_mult()               # everything shown / kept in SEEDS
        done, total = done * mult, total * mult
        now = time.perf_counter()
        elapsed = self._elapsed_before + (now - self._t0)
        done_all = self._done_before + done
        # speed (03/10): the engines report in blocks — a slow search moves its
        # count every few seconds only, and the old 3 s window then showed 0 /s
        # most of the time. Points are kept when the count CHANGES; the speed is
        # measured from the oldest kept point (≥ 5 s back when possible) to now.
        if not self._rate:                    # (reset after a GPU calibration too)
            self._rate.append((now, done_all))
        if done_all != self._rate[-1][1]:
            self._rate.append((now, done_all))
        while len(self._rate) > 2 and now - self._rate[1][0] > 5.0:
            self._rate.pop(0)
        sps = 0.0
        t0r, d0r = self._rate[0]
        if now > t0r and done_all > d0r:
            sps = (done_all - d0r) / (now - t0r)
        # the engine reports families in 48-bit family mode: converted to seeds
        # above (26/09: the part before a pause was in seeds, the live part in
        # families -> mixed units after a resume); across pauses the earlier
        # part is added on both sides.
        unit = "seeds"
        total_all = (self._done_before + total) if total > 0 else 0
        frac = (done_all / total_all) if total_all else None
        eta = (total_all - done_all) / sps if (sps > 0 and total_all) else None
        kind, val = self.rule
        if kind == "time" and val > 0:
            frac = min(1.0, elapsed / val)
            eta = max(0.0, val - elapsed)
            if elapsed >= val and self.state == "running":
                if self.can_pause:
                    self.pause("time")
                else:
                    self.stop()
        elif kind == "results" and val > 0:
            frac = min(1.0, self.found / val)
            if getattr(self, "_limit_hit", False) and self.state == "running":
                self._limit_hit = False
                if self.can_pause:
                    self.auto_paused = "results"
                    self._set_state("pausing")
                else:
                    self._set_state("stopping")
        if getattr(self, "_safety_hit", False) and self.state == "running":
            self._limit_hit = False
            if self.can_pause:
                self.auto_paused = "safety"
                self._set_state("pausing")
            else:
                self._set_state("stopping")
        self.progress.emit(dict(done=done_all, total=total_all, frac=frac, sps=sps, eta=eta,
                                found=self.found, elapsed=elapsed, unit=unit))
        if self._done_flag:
            self._finish(done)

    def profile(self):
        """(rows, total): per active condition [seeds reached, seeds passed,
        own CPU ticks], all runs of this search (pauses included) — or None."""
        base, live = self.profile_base, self._prof_live
        if live is None:
            return base
        if base is None or len(base[0]) != len(live[0]):
            return live
        rows = [[x + y for x, y in zip(a, b)] for a, b in zip(base[0], live[0])]
        return rows, base[1] + live[1]

    def _unit_mult(self):
        ints = [m for m in self._engine_modes if isinstance(m, int)]
        return FAMILY if (ints and ints[-1] == 1) else 1

    def summary(self):
        """'1 234 567 seeds searched · 12 min 03 s · avg 1.7 k/s · 3 found' —
        what the whole search (all its pauses) did so far."""
        from .sessions_page import fmt_int, fmt_rate, fmt_time
        el = self._elapsed_before
        parts = [f"{fmt_int(self._done_before)} seeds searched", fmt_time(el)]
        if el > 0.5 and self._done_before:
            parts.append("avg " + fmt_rate(self._done_before / el))
        parts.append(f"{fmt_int(self.found)} found")
        return "  ·  ".join(parts)

    def _finish(self, done):
        self._timer.stop()                     # `done` is already in seeds (_pump)
        with self._lock:
            batch, self._buf = self._buf, []
        if batch:
            self.resultsReady.emit(batch)
        self._elapsed_before += time.perf_counter() - self._t0
        self.profile_base = self.profile()
        self._prof_live = None
        if self._mode_err:
            self.resume_point = None
            self._set_state("error")
            self.failed.emit(self._mode_err)
            return
        self._done_before += done
        eff = self._eff_now() or self.params.get("engine_mode") or self.params["mode"]
        if eff in ("inc", "blocks"):
            self.eff_mode = eff
        self._merge_overflow(eff)
        if self.resume_point is not None and self.params["seed_list"] is None:
            # exact count: everything before the resume point is fully searched
            # (re-tested seeds after a pause are not counted twice). Offsets are
            # taken modulo 2^64: an Incremental scan may cross INT64_MAX.
            if eff == "inc":
                self._done_before = (self.resume_point - self.base_start) % U64
            else:
                self._done_before = ((self.resume_point - (self.base_start & M48)) % U64) * FAMILY
        if self._err:
            self._set_state("error")
            self.failed.emit(self._err)
            return
        if self.state == "pausing" and self.rule[0] == "seeds" and self.limit_reached():
            self._set_state("stopping")        # paused on the very last seed: finished
        summ = self.summary()
        if self.state == "stopping" and self.rule[0] == "seeds" and self.limit_reached():
            self._set_state("done")
            self.message.emit(f"Done  ·  {summ}")
            return
        if self.state == "pausing" or (getattr(self, "_limit_hit", False) and self.can_pause):
            self._limit_hit = False
            why = {"time": "time limit reached", "results": "result limit reached",
                   "safety": f"safety limit reached (over {self._safety():,} results)".replace(",", " ")}.get(
                self.auto_paused, "paused")
            if self.resume_point is not None and self.can_pause:
                self._set_state("paused")
                self.message.emit(f"{why[0].upper() + why[1:]}  ·  {summ}  ·  "
                                  f"resume from {self.resume_point}")
            else:
                self._set_state("done")
                self.message.emit(f"Stopped (this engine cannot resume)  ·  {summ}")
        elif self.state == "stopping":
            self._set_state("done")
            self.message.emit(f"Stopped  ·  {summ}")
        else:
            self._set_state("done")
            self.message.emit(f"Done  ·  {summ}")

    def _refind_count(self) -> int:
        """Matches already shown that this run will find AGAIN (a resumed run
        re-tests the chunks that were in progress): the engine's own match cap
        must leave room for them."""
        if not self._seen:
            return 0
        rs, b = int(self._run_start), int(getattr(self, "base_start", self._run_start))
        if self.params.get("engine_mode", "inc") == "inc":
            lim = (rs - b) % U64
            again = lambda sd: (sd - b) % U64 >= lim
        else:
            m = 1 << 48
            lim = (rs - (b & M48)) % m
            again = lambda sd: ((sd & M48) - (b & M48)) % m >= lim
        n = 0
        with self._lock:
            for key in self._seen:
                sd = key[0] if isinstance(key, tuple) else key
                if again(int(sd)):
                    n += 1
        return n

    def _merge_overflow(self, eff):
        """Matches that arrived after the result limit (see on_match): keep the
        ones the resumed search would not find again."""
        with self._lock:
            extra, self._overflow = self._overflow, []
        if not extra or self.resume_point is None or not self.can_pause:
            return                              # stopped: the limit is what was asked
        rp, b = int(self.resume_point), int(self.base_start)
        if eff == "inc":
            lim = (rp - b) % U64
            before = lambda sd: (sd - b) % U64 < lim
        elif eff in ("fam48", "blocks"):
            m = 1 << 48
            lim = (rp - (b & M48)) % m
            before = lambda sd: ((sd & M48) - (b & M48)) % m < lim
        else:
            before = lambda sd: True            # unknown order: never lose a result
        batch = []
        with self._lock:
            for key, sd, det in extra:
                if key in self._seen or not before(sd):
                    continue
                self._seen.add(key)
                self.found += 1
                batch.append((sd, det))
        if batch:
            self.resultsReady.emit(batch)

    def _set_state(self, st):
        self.state = st
        self.stateChanged.emit(st)
