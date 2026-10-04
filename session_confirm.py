"""
session_confirm.py — the Minecraft confirmation of the Sessions "Broken portal"
condition (03/10).

A session result passed the predicted test (ruined portal footprint / lake rule)
in custom_search._stronghold_extra_ok. Here we rebuild, for that seed, the exact
checker requests the Stronghold page sends to mc_checker (ruined portal:
ruined_portal.corrupt_request_for; lakes: lake_portals.lake_candidates), for the
first stronghold or for each of the 128 that pass the condition.

Runs in a separate process (one worker): ruined_portal / lake_portals keep a
module-level generator context that the search may be using in the GUI process.

  requests_for(seed, mc, params, large) -> [dict(frames, rox, roz, sh, cause, n)]
"""
from __future__ import annotations

import types


def requests_for(seed, mc, params, large=False):
    import cubiomes_wrap
    import custom_search as cs
    import lake_portals as lp
    import ruined_portal as rp
    seed, mc = int(seed), int(mc)
    srcs = params.get("broken_sources") or ["ruined"]
    kinds = tuple(k for k in ("lava", "water") if k in srcs)
    cubiomes_wrap.set_large_biomes(bool(large))      # this process lives on: follow the GUI
    if not lp._W or lp._W.get("mc") != mc:
        lp._winit(mc)
    cubiomes_wrap.apply_large_ctx(lp._W["lib"], lp._W["ctx"])
    lp._W.pop("seed", None)
    rp._ensure_local_ctx(mc)
    cubiomes_wrap.apply_large_ctx(rp._W["lib"], rp._W["ctx"])
    positions = lp._strongholds(seed, cs._scan_val(params))
    ctx = types.SimpleNamespace(seed=seed, mc_version=mc)
    out = []
    for sx, sz in positions:
        try:
            if not cs._stronghold_extra_ok(ctx, params, int(sx), int(sz)):
                continue
        except Exception:
            continue
        sh = (int(sx) >> 4, int(sz) >> 4)
        if "ruined" in srcs and mc <= 21:
            try:
                r = rp.corrupt_check_at(seed, int(sx), int(sz), mc)
                rq = None
                if r and int(r.get("corruptible", 0) or 0) > 0:
                    rp._ensure_local_ctx(mc)
                    rq = rp.corrupt_request_for(seed, mc, sx=int(sx), sz=int(sz),
                                                _ctxlib=(rp._W["lib"], rp._W["ctx"]))
                if rq is not None:
                    out.append(dict(frames=[tuple(map(int, f)) for f in rq[0]],
                                    rox=int(rq[3]), roz=int(rq[4]),
                                    sh=getattr(rq, "sh", None) or sh,
                                    cause="ruined portal",
                                    n=int(r.get("corruptible", 0) or 0)))
            except Exception:
                pass
        if kinds and mc in lp.LAKE_VERSIONS:
            try:
                lp._W["lib"].cb_setup(lp._W["ctx"], mc, seed & ((1 << 64) - 1))
                lp._W["seed"] = seed
                for d in lp.lake_candidates(seed, int(sx), int(sz), mc, kinds):
                    out.append(dict(frames=[tuple(map(int, f)) for f in d["frames"]],
                                    rox=int(d["trig"][0]), roz=int(d["trig"][1]), sh=sh,
                                    cause=f"{d['kind']} lake", n=int(d["npred"])))
            except Exception:
                pass
        if len(out) >= 6:                      # enough to decide (All 128: rare)
            break
    return out
