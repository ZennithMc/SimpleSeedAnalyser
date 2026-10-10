"""
gpu_planner.py — "GPU: Auto" for custom sessions.

The GPU never replaces the CPU in SSA: it PRE-FILTERS the seeds on ONE kind of
condition (structures, a biome sample, a climate extreme…) and the C engine
re-checks every survivor with the whole session, so results are always exact.
What decides the speed is WHICH condition the GPU filters on (and whether the
GPU helps at all). This module measures instead of guessing:

  1. candidates: CPU only; the structure pipeline (all structure conditions);
     each top-level condition a GPU pipeline accepts (evaluate_session's own
     eligibility functions decide — nothing is duplicated here);
  2. many candidates → ranked by how selective they are (share of seeds they
     let through, measured with the C engine on a small sample), best 3 kept;
  3. each remaining candidate runs the REAL search for a few seconds; the
     fastest wins (a GPU plan must beat the CPU by 15 %, otherwise CPU).

plan_gpu(...) → Plan(conditions, label, detail, report). The chosen
configuration is only a copy of the conditions with the use_gpu flags set:
the search itself is the normal one. Plans are cached per (session, version,
mode, threads).
"""
from __future__ import annotations

import copy
import json
import threading
import time
from dataclasses import dataclass, field

import custom_search as cs

GPU_TYPES_1_18 = ("biome_sample", "biome_filter", "biome_center", "climate_minmax",
                  "noise_sample")   # climate_center (flood fill): CPU only for now (01/10)
MARK = "_gpu_plan_id"
BENCH_STEADY = 3.5            # seconds of measurement after the first progress
BENCH_MAX = 14.0              # hard cap per candidate (CUDA warm-up included)
BENCH_MAX_GPU = 22.0          # GPU lines: CUDA start-up + first batches
MIN_GAIN = 1.15               # a GPU plan must be 15 % faster than CPU only
MAX_BENCH = 3                 # benchmarked GPU candidates (besides structures / crunch)
_CACHE: dict = {}


@dataclass
class Plan:
    conditions: list
    label: str                               # short, for the engine chip
    detail: str                              # one line, for the status / tooltip
    report: list = field(default_factory=list)   # [(name, seeds_per_s | None)]
    excluded: list = field(default_factory=list)  # [(condition name, why no GPU)]
    mode: str = ""                           # search mode the plan was measured in
    best: str = "CPU only"                   # name (in report) of the chosen line
    modes: list = field(default_factory=list)  # [(mode, best rate)] when modes were compared
    notes: dict = field(default_factory=dict)  # name -> why a GPU line has no speed


def _name(c):
    lab = (c.get("label") or "").strip()
    t = c.get("type", "?")
    p = c.get("params", {})
    if lab:
        return lab
    if t in ("climate_minmax", "climate_center", "noise_sample"):
        return f"{t.replace('_', ' ')} ({p.get('para', '')})"
    if t in ("biome_sample", "biome_filter", "biome_center"):
        inc = p.get("biomes_incl") or p.get("biome_id") or ""
        if isinstance(inc, (list, tuple)):
            inc = ", ".join(str(b) for b in inc[:2]) + ("…" if len(inc) > 2 else "")
        return f"{t.replace('_', ' ')} {inc}".strip()
    return t.replace("_", " ")


def _gpu_capable(c):
    t = c.get("type", "")
    return (t in cs._structure_type_keys() or t in GPU_TYPES_1_18 or t == "crunchiness"
            or "use_gpu" in (c.get("params") or {}))


def strip_gpu(conditions):
    """Copy of the session with every GPU flag off (= CPU only)."""
    out = copy.deepcopy(conditions)
    for c in out:
        if _gpu_capable(c):
            c.setdefault("params", {})["use_gpu"] = False
    return out


def _gpu_available():
    ok = {}
    for mod, fn in (("structure_cuda", "is_available"), ("noise_gpu", "available"),
                    ("gaider_cuda", "is_available")):
        try:
            m = __import__(mod)
            ok[mod] = bool(getattr(m, fn)())
        except Exception:
            ok[mod] = False
    return ok


# ── zone englobante (bounding zone) of a DEPENDENT condition ─────────────────
# A child condition's area is centred on its parent's position, which changes
# with every seed. But that position always lies in a box we can compute by
# walking up the chain (a structure is inside its area, a spiral stays in its
# rectangle, ×8 / ÷8 for the dimension scales…). The union of every place the
# child's area can be is then a FIXED box: "the biome exists somewhere in it"
# is a NECESSARY condition → a sound GPU pre-filter (the C engine still checks
# every survivor with the exact position). Only for tests of the kind "there
# is at least one point that…"; coverage tests (a % of the area) cannot widen.
UNION_SOUND = ("climate_minmax", "biome_filter", "biome_center")
GPU_GRID_MAX = 1 << 20          # cells at scale 4 accepted for a zone englobante (~4 100×4 100 blocks)
MARGIN = 16
# conditions whose pushed position is their parent's or a point of their area
# (not climate_center: its region's centre can lie ±128 cells away when its area is 0)
_MOVES_IN_AREA = {"spiral", "climate_minmax", "biome_center", "biome_filter",
                  "biome_sample", "noise_sample", "climate_noise", "slime", "height",
                  "logic_or", "logic_not", "crunchiness"}


def _area_rel(c):
    r = int(c.get("rmax", 0) or 0)
    if r > 0:
        r -= 1
        return (-r, -r, r, r)
    x1, z1, x2, z2 = (int(c.get(k, 0) or 0) for k in ("x1", "z1", "x2", "z2"))
    return (min(x1, x2), min(z1, z2), max(x1, x2), max(z1, z2))


def position_boxes(act):
    """For each active condition, the box of positions it can hand to its
    children, or None when unbounded (strongholds, spawn…)."""
    n = len(act)
    memo = {}
    skeys = cs._structure_type_keys()

    def parent_box(i):
        r = int(act[i].get("relative", 0) or 0)
        return (0, 0, 0, 0) if r == 0 else (box(r - 1) if 1 <= r <= n else None)

    def box(i):
        if i in memo:
            return memo[i]
        memo[i] = None                       # cycle guard
        P = parent_box(i)
        t = act[i].get("type", "")
        out = None
        if P is not None:
            if t == "scale_to_overworld":
                out = (P[0] * 8, P[1] * 8, P[2] * 8, P[3] * 8)
            elif t == "scale_to_nether":
                q = lambda v: int(v / 8)      # C division truncates toward 0 (monotone)
                out = (q(P[0]), q(P[1]), q(P[2]), q(P[3]))
            elif t in _MOVES_IN_AREA or t in skeys:
                a = _area_rel(act[i])
                out = (min(P[0], P[0] + a[0]) - MARGIN, min(P[1], P[1] + a[1]) - MARGIN,
                       max(P[2], P[2] + a[2]) + MARGIN, max(P[3], P[3] + a[3]) + MARGIN)
        memo[i] = out
        return out
    return [box(i) for i in range(n)], parent_box


def zone_englobante(act, i):
    """Absolute box covering every place condition i's area can be, or None."""
    _boxes, parent_box = position_boxes(act)
    P = parent_box(i)
    if P is None:
        return None
    a = _area_rel(act[i])
    return (P[0] + a[0] - 4, P[1] + a[1] - 4, P[2] + a[2] + 4, P[3] + a[3] + 4)


def _under_or_not(act, i):
    """Is condition i below a logic_or / logic_not? (then it is not NECESSARY)"""
    seen = set()
    r = int(act[i].get("relative", 0) or 0)
    while r and r not in seen and 1 <= r <= len(act):
        seen.add(r)
        if act[r - 1].get("type") in ("logic_or", "logic_not"):
            return True
        r = int(act[r - 1].get("relative", 0) or 0)
    return False


def _cells(b):
    return (((b[2] - b[0]) >> 2) + 1) * (((b[3] - b[1]) >> 2) + 1)


def _why_not(c, mc, mode, avail):
    """Why a GPU-capable condition cannot be a GPU pre-filter (plain words)."""
    t = c.get("type", "")
    p = c.get("params", {}) or {}
    if t == "crunchiness":
        if not avail["gaider_cuda"]:
            return "no usable CUDA GPU"
        if mode != "inc":
            return "the GPU crunchiness filter scans seed by seed (not in 48-bit family mode)"
        return "it is not at the top level of the session (under another condition)"
    if not avail["noise_gpu"]:
        return "no usable CUDA GPU"
    if int(mc) < 22:
        return "GPU biome / climate filters need Java 1.18+"
    if mode != "inc":
        return "GPU biome / climate filters scan seed by seed (not in 48-bit family mode)"
    if t in ("climate_minmax", "climate_center", "noise_sample") and int(p.get("nmax", -1) or -1) > 0:
        try:
            import noise_gpu as _ng
            if not _ng.has_climate_nmax():
                return ("limited octaves need the newer GPU library "
                        "(py tools/build_native.py --unit noise_gpu)")
        except Exception:
            return "limited octaves (“up to octave”) are not computed by the GPU"
    if t in ("biome_sample", "biome_filter") and not p.get("biomes_incl"):
        return "no required biome to look for"
    if t == "biome_filter" and str(p.get("blocks_scale", "")).startswith("1/1"):
        return "1:1 block scale is checked on the CPU"
    if int(c.get("instances", 1) or 0) <= 0:
        return "exclusion (0 instances) is checked on the CPU"
    if int(c.get("relative", 0) or 0) != 0:
        if t not in UNION_SOUND:
            return ("depends on another condition, and its test (a share / coverage of its "
                    "area) cannot be widened to a fixed zone for the GPU")
        return "depends on another condition (see its zone englobante)"
    return "these options are not supported by the GPU filter"


def _candidates(conditions, mc, mode="inc", excluded=None):
    """[(name, conditions_copy, kind, orig_index)] — every configuration that
    really puts the GPU to work (checked with evaluate_session's own rules).
    `excluded` (list) receives (name, reason) for GPU-capable conditions that
    cannot be a GPU pre-filter."""
    excluded = excluded if excluded is not None else []
    avail = _gpu_available()
    base = strip_gpu(conditions)
    for i, c in enumerate(base):
        c.setdefault("params", {})[MARK] = i
    out = []
    # structures: one plan, the GPU filters them all together (build_jobs)
    skeys = cs._structure_type_keys()
    sidx = [i for i, c in enumerate(base) if c.get("type") in skeys and c.get("enabled", True)]
    if sidx and not avail["structure_cuda"]:
        excluded.append(("structures", "no usable CUDA GPU (structure_cuda)"))
    if sidx and avail["structure_cuda"]:
        cfg = copy.deepcopy(base)
        for i in sidx:
            cfg[i]["params"]["use_gpu"] = True
        names = sorted({base[i].get("type", "").replace("_", " ") for i in sidx})
        out.append(("structures: " + ", ".join(names[:3]) + ("…" if len(names) > 3 else ""),
                    cfg, "struct", -1))
    # one top-level condition per GPU pipeline — incremental scans only: these
    # pipelines scan seeds one by one, they would silently turn a 48-bit
    # family search into an incremental one
    eligible = (cs._biome_gpu_eligible, cs._biome_filter_gpu_eligible,
                cs._biome_center_gpu_eligible, cs._climate_gpu_eligible,
                cs._noise_gpu_eligible)
    for i, c in enumerate(base):
        if c.get("type") not in GPU_TYPES_1_18 or not c.get("enabled", True):
            continue
        if not (int(mc) >= 22 and avail["noise_gpu"] and mode == "inc"):
            excluded.append((f"#{i + 1} {_name(c)}", _why_not(c, mc, mode, avail)))
            continue
        cfg = copy.deepcopy(base)
        cfg[i]["params"]["use_gpu"] = True
        zname = ""
        if int(c.get("relative", 0) or 0) != 0 and c.get("type") in UNION_SOUND:
            act0 = cs.active_conditions(base)
            k = next((j for j, a in enumerate(act0) if a.get("params", {}).get(MARK) == i), None)
            if k is not None and _under_or_not(act0, k):
                excluded.append((f"#{i + 1} {_name(c)}", "inside an OR / NOT: not required by "
                                 "itself, so it cannot filter seeds out"))
                continue
            z = zone_englobante(act0, k) if k is not None else None
            if z is None:
                excluded.append((f"#{i + 1} {_name(c)}", "depends on a condition whose "
                                 "position is not bounded (stronghold, spawn…): no fixed zone "
                                 "for the GPU"))
                continue
            if _cells(z) > GPU_GRID_MAX:
                excluded.append((f"#{i + 1} {_name(c)} (zone {z[2] - z[0]:,} × {z[3] - z[1]:,})"
                                 .replace(",", " "),
                                 "its zone englobante is too large for the GPU filter "
                                 "(max ≈ 4 100 × 4 100 blocks)"))
                continue
            cfg[i]["params"]["gpu_box"] = list(z)
            zname = f" · zone {z[2] - z[0]}×{z[3] - z[1]}"
        act = cs.active_conditions(cfg)
        picked = None
        for f in eligible:
            try:
                picked = f(act)
            except Exception:
                picked = None
            if picked is not None:
                break
        if picked is not None and picked.get("params", {}).get(MARK) == i:
            out.append((f"#{i + 1} {_name(c)}{zname}", cfg, "cond", i))
        else:
            excluded.append((f"#{i + 1} {_name(c)}", _why_not(c, mc, mode, avail)))
    # crunchiness: a top-level one (08/10: with a spiral too — the survivors are
    # validated by all the CPU threads now)
    ci = next((i for i, c in enumerate(base) if c.get("type") == "crunchiness"
               and c.get("enabled", True) and int(c.get("relative", 0) or 0) == 0), None)
    if ci is not None and avail["gaider_cuda"] and mode == "inc":
        cfg = copy.deepcopy(base)
        cfg[ci]["params"]["use_gpu"] = True
        out.append(("crunchiness", cfg, "crunch", ci))
    elif ci is not None:
        excluded.append(("crunchiness", _why_not(base[ci], mc, mode, avail)))
    for _n, cfg, _k, _i in out:                       # the marker is not a real parameter
        for c in cfg:
            c.get("params", {}).pop(MARK, None)
    return out


def _pass_rate(cond, mc, sample, budget_s, stop=None):
    """Share of `sample` seeds a condition lets through ALONE (C engine)."""
    import ctypes as ct
    from cubiomes_wrap import CubiomesLib
    c = copy.deepcopy(cond)
    c["relative"] = 0
    p = c.setdefault("params", {})
    p["use_gpu"] = False
    gb = p.pop("gpu_box", None)
    if gb:                                   # the widened test: its zone as a fixed area
        c["x1"], c["z1"], c["x2"], c["z2"] = (int(v) for v in gb)
        c["rmax"] = 0; c["radial"] = 0; c["area_mode"] = 2
    lib = CubiomesLib()
    raw = lib._lib
    res = cs._build_ssacond_array([c], raw)
    if res is None:
        return 1.0
    arr, n = res
    raw.ssa_test_seeds_batch.argtypes = [ct.c_void_p, ct.c_int, ct.c_int, ct.c_int,
                                         ct.POINTER(ct.c_int64), ct.c_int64,
                                         ct.POINTER(ct.c_int64), ct.c_int64]
    raw.ssa_test_seeds_batch.restype = ct.c_int64
    try:
        from cubiomes_wrap import get_large_biomes
        large = 1 if get_large_biomes() else 0
    except Exception:
        large = 0
    kept = tested = 0
    t0 = time.perf_counter()
    # small batches: one C call over a zone of ~2 000 blocks can take a
    # while per seed; the budget and Stop are checked between batches
    k, step = 0, 1                     # start with ONE seed, then ~0.1 s per call
    while k < len(sample):
        if stop is not None and stop.is_set():
            break
        part = sample[k:k + step]
        ins = (ct.c_int64 * len(part))(*part)
        outs = (ct.c_int64 * len(part))()
        t1 = time.perf_counter()
        kept += int(raw.ssa_test_seeds_batch(arr, n, int(mc), large, ins, len(part), outs, len(part)))
        dt = (time.perf_counter() - t1) / len(part)
        tested += len(part); k += len(part)
        step = max(1, min(64, int(0.1 / max(dt, 1e-6))))
        if time.perf_counter() - t0 > budget_s:
            break
    return (kept + 0.5) / (tested + 1.0)


def _bench(conditions, start, mc, threads, mode, stop_outer, cap=BENCH_MAX):
    """Seeds (or families) per second of the real search with this config;
    None if the GPU pipeline did not engage / nothing measurable."""
    ev = threading.Event()
    marks, modes, err = [], [], []

    def prog(done, _total):
        marks.append((time.perf_counter(), int(done)))

    def run():
        try:
            cs.evaluate_session(conditions, int(start), 0, int(mc), int(threads), ev,
                                on_match=lambda s, d="": None, on_progress=prog,
                                search_mode=mode, on_mode=modes.append)
        except Exception as e:
            err.append(e)
    th = threading.Thread(target=run, name="ssa-gpu-bench", daemon=True)
    t0 = time.perf_counter()
    th.start()
    while th.is_alive():
        time.sleep(0.05)
        now = time.perf_counter()
        if stop_outer is not None and stop_outer.is_set():
            break
        first = next((t for t, d in marks if d > 0), None)
        if (first is not None and now - first >= BENCH_STEADY) or now - t0 >= cap:
            break
    ev.set()
    th.join(timeout=30)
    if err or th.is_alive():
        return None, modes
    pts = [(t, d) for t, d in marks if d > 0]
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < 0.4:
        # a single report: rate from the start (slow engines report rarely)
        if pts and pts[-1][0] - t0 > 0.4:
            return pts[-1][1] / (pts[-1][0] - t0), modes
        return None, modes
    return (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0]), modes


def why_not_engaged(modes, engaged, rate):
    """Plain-words reason for a GPU line without a measured speed."""
    if engaged and not rate:
        return (f"the GPU ran, but no progress was measured within {BENCH_MAX_GPU:.0f} s "
                "(a very slow session: try a longer analysis or a smaller area)")
    if engaged:
        return None
    msgs = [str(m[1]) for m in modes if isinstance(m, tuple) and m and m[0] == "gpu_status"]
    if msgs:
        return msgs[-1]
    ints = [m for m in modes if isinstance(m, int)]
    if 3 in ints:
        return "the GPU pipeline stopped and handed the search back to the CPU"
    return "the GPU pipeline did not start for this condition (the C engine ran instead)"


def gpu_engaged(modes):
    """True if a GPU pipeline really ran. The pipelines announce mode 3 before
    trying; if the GPU is not usable they fall back to the C engine, which
    then reports its own mode (0 / 1 / 2) — so only a LAST mode of 3 counts."""
    ints = [m for m in modes if isinstance(m, int)]
    return bool(ints) and ints[-1] == 3


MODE_NAMES = {"inc": "Incremental", "fam48": "48-bit family"}


def _x(r):
    """1.5 · 9.5 · 42 · 2 800"""
    return f"{r:.1f}" if r < 10 else f"{r:,.0f}".replace(",", " ")


def plan_modes(conditions, mc, start, threads, modes=("inc", "fam48"), current="inc",
               status=None, stop=None, use_cache=True, events=None) -> Plan:
    """plan_gpu in each search mode, then the fastest mode overall. Seeds per
    second are comparable across modes (the engine reports 64-bit seeds in
    both). The current mode is kept unless another one is 15 % faster.
    Event names are prefixed by the mode ("48-bit family · CPU only")."""
    ev = events or (lambda *_a, **_k: None)
    plans = []
    for m in modes:
        if stop is not None and stop.is_set():
            break
        pre = MODE_NAMES.get(m, m) + " · "

        def sub(kind, _pre=pre, **d):
            if kind == "done":
                return
            d = dict(d)
            if "name" in d:
                d["name"] = _pre + d["name"]
            if "names" in d:
                d["names"] = [_pre + n for n in d["names"]]
            if kind == "candidates":
                d["prefix"] = _pre
            ev(kind, **d)
        ev("mode", mode=m, title=MODE_NAMES.get(m, m))
        plans.append(plan_gpu(conditions, mc, start, threads, m, status=status, stop=stop,
                              use_cache=use_cache, events=sub, measure_cpu=True))
    if not plans:
        return None

    def top(p):
        return max([r or 0 for _n, r in p.report] + [0])
    cur = next((p for p in plans if p.mode == current), plans[0])
    win = max(plans, key=top)
    if win is not cur and top(win) < top(cur) * MIN_GAIN:
        win = cur
    report, excluded, notes = [], [], {}
    for p in plans:
        pre = MODE_NAMES.get(p.mode, p.mode) + " · "
        report += [(pre + n, r) for n, r in p.report]
        notes.update({pre + n: w for n, w in getattr(p, "notes", {}).items()})
        excluded += [(n, w) for n, w in p.excluded if (n, w) not in excluded]
    out = Plan(win.conditions, win.label, win.detail, report,
               [e for e in win.excluded], win.mode,
               MODE_NAMES.get(win.mode, win.mode) + " · " + win.best,
               [(p.mode, top(p)) for p in plans], notes)
    if win.mode != current and top(cur):
        def how(p):                          # "GPU" / "CPU": what the best line of p is
            b = max(p.report, key=lambda nr: nr[1] or 0)[0]
            return "CPU" if b == "CPU only" else "GPU"
        cpu_cur = dict(cur.report).get("CPU only") or 0
        txt = (f"{MODE_NAMES.get(win.mode)} ({how(win)}) is ×{_x(top(win) / top(cur))} faster "
               f"than the best {MODE_NAMES.get(current)} option ({how(cur)})")
        if how(cur) == "GPU" and cpu_cur:
            txt += f" and ×{_x(top(win) / cpu_cur)} faster than {MODE_NAMES.get(current)} on the CPU"
        out.detail = txt + ". " + win.detail
    ev("done", plan=out)
    return out


def plan_gpu(conditions, mc, start, threads, mode="inc", status=None, stop=None,
             use_cache=True, events=None, measure_cpu=False) -> Plan:
    """Choose CPU only or the best GPU pre-filter for this session (measured).
    events(kind, **data) — live feed for the analysis window:
      ("candidates", names=[…], excluded=[…]), ("measuring", name=…),
      ("measured", name=…, rate=…|None, engaged=bool), ("done", plan=…)."""
    say = status or (lambda _t: None)
    ev = events or (lambda *_a, **_k: None)
    key = (json.dumps(cs.active_conditions(conditions), sort_keys=True, default=str),
           int(mc), mode, int(threads), bool(measure_cpu))
    if use_cache and key in _CACHE:
        ev("done", plan=_CACHE[key])
        return _CACHE[key]
    cpu = strip_gpu(conditions)
    excluded = []
    cands = _candidates(conditions, mc, mode, excluded)
    ev("candidates", names=[c[0] for c in cands], excluded=list(excluded))
    if not cands and measure_cpu:          # modes compared: the CPU speed is needed
        say("Measuring the CPU…")
        ev("measuring", name="CPU only")
        r, _m = _bench(cpu, start, mc, threads, mode, stop)
        ev("measured", name="CPU only", rate=r, engaged=True)
        plan = Plan(cpu, "Auto → CPU", "No condition of this session can be filtered on the "
                    "GPU: C engine on the CPU.", [("CPU only", r)], excluded, mode)
        if not (stop is not None and stop.is_set()):
            _CACHE[key] = plan
        ev("done", plan=plan)
        return plan
    if not cands:
        plan = Plan(cpu, "Auto → CPU", "No condition of this session can be filtered on the "
                    "GPU: C engine on the CPU (nothing to measure).", [], excluded, mode)
        _CACHE[key] = plan
        ev("done", plan=plan)
        return plan
    # too many single-condition candidates → keep the most selective ones
    singles = [c for c in cands if c[2] == "cond"]
    if len(singles) > MAX_BENCH:
        say(f"GPU auto: ranking {len(singles)} conditions…")
        import random
        rnd = random.Random(int(start) ^ 0x5EED)
        sample = [rnd.getrandbits(63) for _ in range(512)]
        act_by_idx = {cand[3]: cand[1][cand[3]] for cand in singles}
        scored = []
        per = max(0.15, 6.0 / len(singles))
        for cand in singles:
            if stop is not None and stop.is_set():
                break
            scored.append((_pass_rate(act_by_idx[cand[3]], mc, sample, per, stop), cand))
        scored.sort(key=lambda x: x[0])
        keep = {id(c) for _r, c in scored[:MAX_BENCH]}
        for r_, c_ in scored[MAX_BENCH:]:
            excluded.append((c_[0], f"less selective than the others (lets {r_:.1%} of seeds "
                                    f"through): not measured"))
        cands = [c for c in cands if c[2] != "cond" or id(c) in keep]
        ev("candidates", names=[c[0] for c in cands], excluded=list(excluded))
    report = []
    notes = {}
    say("GPU auto: measuring the CPU…")
    ev("measuring", name="CPU only")
    cpu_rate, _m = _bench(cpu, start, mc, threads, mode, stop)
    report.append(("CPU only", cpu_rate))
    ev("measured", name="CPU only", rate=cpu_rate, engaged=True)
    best = ("CPU only", cpu, cpu_rate or 0.0)
    for k, (name, cfg, _kind, _i) in enumerate(cands, 1):
        if stop is not None and stop.is_set():
            break
        say(f"GPU auto: trying {k}/{len(cands)} — {name}…")
        ev("measuring", name=name)
        rate, modes = _bench(cfg, start, mc, threads, mode, stop, cap=BENCH_MAX_GPU)
        engaged = gpu_engaged(modes)
        report.append((name, rate if engaged else None))
        why = why_not_engaged(modes, engaged, rate)
        if why:
            notes[name] = why
        ev("measured", name=name, rate=rate if engaged else None, engaged=engaged, why=why)
        if engaged and rate and rate > best[2]:
            best = (name, cfg, rate)
    if best[0] != "CPU only" and cpu_rate and best[2] < cpu_rate * MIN_GAIN:
        best = ("CPU only", cpu, cpu_rate)
    if best[0] == "CPU only":
        plan = Plan(cpu, "Auto → CPU", "The GPU does not speed this session up (measured): "
                    "C engine on the CPU.", report, excluded, mode, "CPU only", notes=notes)
    else:
        gain = best[2] / cpu_rate if cpu_rate else 0
        plan = Plan(best[1], f"Auto → GPU · {best[0]}",
                    f"GPU pre-filter on “{best[0]}”" + (f" · ×{gain:.1f} vs CPU" if gain else "")
                    + " (every result re-checked by the C engine).", report, excluded, mode,
                    best[0], notes=notes)
    if not (stop is not None and stop.is_set()):
        _CACHE[key] = plan
    ev("done", plan=plan)
    return plan

