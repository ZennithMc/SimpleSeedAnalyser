"""GPU bench — run on the RTX machine, send back gpu_bench_report.txt.

    py tests/gpu_bench.py            (~10-20 min: every GPU pipeline + your sessions)
    py tests/gpu_bench.py --quick    (~4 min: shorter ranges, no session analysis)
    py tests/gpu_bench.py --probe --cases=3   (biome_sample kernel checked alone first)

For each GPU pre-filter of the session engine (structures, biome sample,
biome filter, biome center, climate min/max, climate noise samples, and the
1.18+ / 1.19.2 variants), the SAME seed range is searched twice:
  * CPU only (C engine, every thread);
  * GPU forced on that condition (GPU pre-filter + C validation).
It reports: did the GPU really run (or why not), seeds per second of each,
and whether both found exactly the same seeds (seeds the GPU lost / added).
Then "GPU: Auto" (the engine analysis) runs on your .session files and the
report shows its measurements and choices.

Read-only: nothing is compiled or written except gpu_bench_report.txt.
Ctrl+C keeps the partial report.
"""
from __future__ import annotations

import copy
import datetime
import glob
import os
import platform
import sys
import threading
import time
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
REPORT = os.path.join(HERE, "gpu_bench_report.txt")
STACKS = os.path.join(HERE, "gpu_bench_stacks.txt")
MAX_RUN_S = 150                   # one search longer than this is stopped (reported)
_lines = []


def out(s=""):
    print(s, flush=True)
    _lines.append(s)
    try:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write("\n".join(_lines) + "\n")
    except Exception:
        pass


def C(t, rel=0, r=0, box=None, inst=1, **params):
    c = {"type": t, "label": "", "enabled": True, "relative": rel, "params": params,
         "instances": inst}
    if r:
        c.update(area_mode=0, radial=r, rmax=r + 1, x1=-r, z1=-r, x2=r, z2=r)
    elif box:
        c.update(area_mode=2, rmax=0, x1=box[0], z1=box[1], x2=box[2], z2=box[3])
    return c


# (name, mc, conditions, index of the condition the GPU filters on)
CASES = [
    ("structures · mansion r300 (1.21)", 28, [C("mansion", r=300)], [0]),
    ("structures · village>outpost (1.21)", 28,
     [C("village", r=300), C("outpost", rel=1, r=250)], [0, 1]),
    ("structures · village r400 (1.19.2)", 23, [C("village", r=400)], [0]),
    ("biome sample · cherry 1% in r1000", 28,
     [C("biome_sample", r=1000, biomes_incl=[185], coverage_pct=1.0, confidence_pct=95.0,
        blocks_scale="1/4 (fast)")], [0]),
    ("biome filter · mushroom fields r800", 28,
     [C("biome_filter", r=800, biomes_incl=[14], blocks_scale="1/4 (fast)")], [0]),
    ("biome center · jungle r1000", 28,
     [C("biome_center", r=1000, biome_id=21, tolerance=4)], [0]),
    ("climate min/max · temperature ≥ 0.7 r1500", 28,
     [C("climate_minmax", r=1500, para="Temperature", locate=0, range=[7000.0, None],
        nmax=-1)], [0]),
    ("climate min/max · large area r2500 (was refused)", 28,
     [C("climate_minmax", r=2500, para="Continentalness", locate=1, range=[None, -9000.0],
        nmax=-1)], [0]),
    ("noise samples · weirdness ≥ 0.6, 30 % of r800", 28,
     [C("noise_sample", r=800, para="Weirdness", range=[6000.0, None], nmax=-1,
        coverage_pct=30.0, confidence_pct=95.0)], [0]),
    ("village → climate (child, zone englobante)", 28,
     [C("village", r=800), C("climate_minmax", rel=1, r=300, para="Temperature", locate=0,
                             range=[6000.0, None], nmax=-1)], [1]),
    ("climate point · cont < -0.145 up to octave 0A (like far_spawn #1)", 28,
     [C("climate_minmax", box=(0, 0, 0, 0), para="Continentalness", locate=0,
        range=[None, -1450.0], nmax=1)], [0]),
    ("climate area · weirdness ≥ 0.7 up to octave 0B, r300", 28,
     [C("climate_minmax", r=300, para="Weirdness", locate=1, range=[7000.0, None],
        nmax=2)], [0]),
]


def search(conds, mc, start, count, threads, gpu_on, stop=None):
    import custom_search as cs
    c = copy.deepcopy(conds)
    for i, x in enumerate(c):
        x["params"]["use_gpu"] = bool(gpu_on) and i in gpu_on
        if gpu_on and i in gpu_on and int(x.get("relative", 0) or 0):
            import gpu_planner as gp            # a child: its zone englobante, as GPU: Auto does
            z = gp.zone_englobante(cs.active_conditions(c), i)
            if z is not None:
                x["params"]["gpu_box"] = list(z)
    found, modes, prog = set(), [], [0]
    ev = stop or threading.Event()
    t = time.perf_counter()

    def on_prog(d, _t):
        prog[0] = d
    def run():
        cs.evaluate_session(c, int(start), int(count), int(mc), int(threads), ev,
                            on_match=lambda s, d="": found.add(int(s)),
                            on_progress=on_prog, search_mode="inc",
                            on_mode=lambda m: modes.append(m))
    th = threading.Thread(target=run, daemon=True)
    th.start()
    t_stop, last_print = None, time.perf_counter()
    while th.is_alive():
        th.join(1.0)
        now = time.perf_counter()
        if now - last_print >= 10:
            last_print = now
            print(f"       {now - t:5.0f} s · {prog[0]:,} seeds · {len(found)} found", flush=True)
        if count and now - t > MAX_RUN_S and not ev.is_set():
            modes.append(("timeout", f"stopped after {MAX_RUN_S} s at {prog[0]:,} seeds"))
            ev.set()
        if ev.is_set() and t_stop is None:
            t_stop = now
        if t_stop is not None and now - t_stop > 20:
            # Stop not honoured: write where every thread is, then give up on it
            import faulthandler
            with open(STACKS, "a", encoding="utf-8") as f:
                f.write(f"\n===== stuck {now - t_stop:.0f} s after Stop · {prog[0]:,} seeds =====\n")
                faulthandler.dump_traceback(file=f, all_threads=True)
            modes.append(("timeout", f"STUCK: Stop not honoured after 20 s (stacks in {os.path.basename(STACKS)})"))
            break
    return found, time.perf_counter() - t, modes, prog[0]


def cpu_rate(conds, mc, start, threads, secs):
    ev = threading.Event()
    res = {}

    def run():
        res["r"] = search(conds, mc, start, 0, threads, None, ev)
    th = threading.Thread(target=run, daemon=True)
    t0 = time.perf_counter()
    th.start()
    time.sleep(secs)
    ev.set()
    th.join(60)
    done = res.get("r", (None, 0, [], 0))[3]
    return done / max(1e-6, time.perf_counter() - t0)


def gpu_state(modes):
    ints = [m for m in modes if isinstance(m, int)]
    msgs = [str(m[1]) for m in modes if isinstance(m, tuple) and m and m[0] == "gpu_status"]
    ran = bool(ints) and ints[-1] == 3
    return ran, (msgs[-1] if msgs else "")


def fmt(r):
    return f"{r / 1e6:.2f} M/s" if r >= 1e6 else f"{r / 1e3:.1f} k/s" if r >= 1e3 else f"{r:.0f} /s"


def section_env():
    out("[ENV]")
    out(f"  {platform.platform()} · Python {platform.python_version()} · {os.cpu_count()} threads")
    for mod, fn, err in (("structure_cuda", "is_available", "get_load_error"),
                         ("noise_gpu", "available", "last_error"),
                         ("gaider_cuda", "is_available", "get_load_error")):
        try:
            m = __import__(mod)
            ok = bool(getattr(m, fn)())
            e = ""
            if not ok and hasattr(m, err):
                e = str(getattr(m, err)())[:300]
            out(f"  {mod:15s} {'OK' if ok else 'NOT AVAILABLE'}  {e}")
        except Exception as ex:
            out(f"  {mod:15s} import failed: {type(ex).__name__}: {ex}")
    try:
        import cubiomes_wrap as cw
        lib = cw.CubiomesLib()._lib
        out(f"  libcubiomes: ssa_eval_single={'yes' if hasattr(lib, 'ssa_eval_single') else 'NO (rebuild)'}"
            f"  dengine={'yes' if hasattr(lib, 'dengine_create') else 'NO (rebuild)'}")
    except Exception as ex:
        out(f"  libcubiomes: {ex}")
    out()


def section_pipelines(threads, quick, only=None):
    out(f"[PIPELINES]  same range, CPU only vs GPU forced · {threads} threads")
    secs = 8 if quick else 20
    bad = 0
    for k, (name, mc, conds, gidx) in enumerate(CASES):
        if only is not None and k not in only:
            continue
        start = 1_000_000_007 * (k + 3)
        try:
            r = cpu_rate(conds, mc, start, threads, 2.5)
            n = int(max(threads * 4, min(200_000_000, r * secs)))
            print(f"  … {name}: CPU run ({n:,} seeds)", flush=True)
            cf, ct, cm, cdone = search(conds, mc, start, n, threads, None)
            print(f"  … {name}: GPU run", flush=True)
            gf, gt, gm, gdone = search(conds, mc, start, n, threads, set(gidx))
            tmo = [m[1] for m in cm + gm if isinstance(m, tuple) and m and m[0] == "timeout"]
            if tmo:
                out(f"  [{k}] {name}")
                out(f"     TIMEOUT — CPU {cdone:,}/{n:,} seeds in {ct:.0f} s, GPU {gdone:,}/{n:,} in "
                    f"{gt:.0f} s ({'; '.join(tmo)})")
                ran, why = gpu_state(gm)
                out(f"     GPU {'ran' if ran else 'not used: ' + (why or 'fell back to the CPU')}")
                bad += 1
                continue
            ran, why = gpu_state(gm)
            lost, extra = len(cf - gf), len(gf - cf)
            same = not lost and not extra
            bad += not same
            out(f"  [{k}] {name}")
            out(f"     {n:,} seeds · CPU {fmt(n / ct)} ({len(cf)} found) · GPU "
                + (f"{fmt(n / gt)} ×{ct / gt:.2f} ({len(gf)} found)" if ran else
                   f"NOT USED — {why or 'fell back to the CPU'} ({fmt(n / gt)})"))
            out("     " + ("same seeds ✓" if same else
                          f"DIFFERENT: GPU lost {lost}, added {extra}  e.g. "
                          f"{sorted(cf - gf)[:3]} / {sorted(gf - cf)[:3]}"))
        except KeyboardInterrupt:
            raise
        except Exception as ex:
            bad += 1
            out(f"  [{k}] {name}: ERROR {type(ex).__name__}: {ex}")
            out("     " + traceback.format_exc().strip().replace("\n", "\n     ")[-900:])
    out(f"  → {bad} case(s) with a difference or an error")
    out()


def section_probe():
    """biome_sample GPU kernel alone, in a CHILD process with a time limit (a
    stuck GPU call cannot block the bench): speed, and its keep / drop
    decisions against the CPU reference (exact FP64 biomes)."""
    import subprocess
    out("[PROBE]  biome_sample GPU kernel (child process, 90 s max)")
    cmd = [sys.executable, os.path.join(HERE, "tests", "biomemc_probe.py")]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90, cwd=HERE)
        txt = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        txt = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")) \
            + ((e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else (e.stderr or "")) \
            + "\nTIMEOUT: the child was killed after 90 s"
    for line in txt.splitlines():
        if line.strip() and "[GPU-DBG]" not in line:
            out("  " + line)
    out()
    return "step exact" in txt           # False: the kernel failed / hung


def section_sessions(threads):
    import cubiomes_session as csn
    import cubiomes_wrap as cw
    import gpu_planner as gp
    files = sorted(set(glob.glob(os.path.join(HERE, "*.session"))
                       + glob.glob(os.path.join(HERE, "tests", "*.session"))))
    try:
        from qtui.session_library import lib_dir
        files += sorted(glob.glob(os.path.join(lib_dir(), "*.session")))[:8]
    except Exception:
        pass
    out(f"[SESSIONS]  GPU: Auto on {len(files)} session file(s)")
    for path in files:
        try:
            d = csn.parse_session_file(path)
            mc = cw.MC_VERSIONS.get(d.get("mc_str") or "", 28)
            cw.set_large_biomes(bool(d.get("large_biomes")))
            conds = d["conditions"]
            t = time.perf_counter()
            plan = gp.plan_gpu(conds, mc, 123_456_789_000, threads, "inc", use_cache=False,
                               measure_cpu=True)
            out(f"  {os.path.basename(path)}  ({d.get('mc_str')}, {len(conds)} conditions, "
                f"{time.perf_counter() - t:.0f} s)")
            out(f"     → {plan.label}: {plan.detail}")
            for nm, rate in plan.report:
                note = (plan.notes or {}).get(nm, "")
                out(f"       · {nm}: " + (fmt(rate) if rate else f"no speed — {note or 'n/a'}"))
            for nm, why in plan.excluded:
                out(f"       ✗ {nm}: {why}")
        except KeyboardInterrupt:
            raise
        except Exception as ex:
            out(f"  {os.path.basename(path)}: ERROR {type(ex).__name__}: {ex}")
        finally:
            try:
                cw.set_large_biomes(False)
            except Exception:
                pass
    out()


def main():
    quick = "--quick" in sys.argv
    threads = os.cpu_count() or 4
    out(f"SSA GPU bench — {datetime.datetime.now():%Y-%m-%d %H:%M}"
        + ("  (quick)" if quick else ""))
    out()
    try:
        section_env()
        only = None
        for a in sys.argv[1:]:
            if a.startswith("--cases="):
                only = {int(x) for x in a.split("=", 1)[1].split(",") if x}
        if "--probe" in sys.argv and not section_probe():
            out("  → the biome_sample GPU kernel did not finish: its pipeline case is skipped")
            out()
            only = (set(only) if only is not None else set(range(len(CASES)))) - {3}
        section_pipelines(threads, quick, only)
        if not quick:
            section_sessions(threads)
    except KeyboardInterrupt:
        out("(interrupted — partial report)")
    out(f"Report: {REPORT}")
    os._exit(0)


if __name__ == "__main__":
    main()
