"""Lake-broken end portals (1.16): the predictor (lake_portals) must still find the
known real breaks, and the Minecraft checker must confirm them.

Truth: tests/lake_portal_truth_1161.json = seed -> frames broken by a lake (first
stronghold, frame chunks first), the 17 real breaks among 60,000 random first
strongholds (03/10). 14 of them pass the candidate rule (3 one- or two-frame breaks
are knowingly left out, see lake_portals.py).

  py tests/lake_portal_check.py          # predictor only (fast)
  py tests/lake_portal_check.py check    # + the Minecraft checker on 4 of them
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lake_portals as lp  # noqa: E402

KNOWN_LEFT_OUT = {2903876214508883104, -2736074088938060761, 2423786975020711934}


def main():
    truth = {int(k): v for k, v in json.load(open(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "lake_portal_truth_1161.json"))).items()}
    lp._winit(20)
    found, bad = {}, 0
    for s in truth:
        for sx, sz in lp._strongholds(s, False):
            c = lp.lake_candidates(s, sx, sz)
            if c:
                found[s] = (c[0], (sx >> 4, sz >> 4))
    for s, n in truth.items():
        ok = (s in found) != (s in KNOWN_LEFT_OUT)
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'}  {s:>21}  {n:>2} frames  "
              + (f"candidate {found[s][0]['kind']} lake" if s in found else "not a candidate"))
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        import mc_checker
        ok, why = mc_checker.status()
        if not ok:
            print("checker SKIPPED:", why)
        else:
            pick = sorted(found)[:4]
            reqs = [(s, found[s][0]["frames"], *found[s][0]["trig"], found[s][1]) for s in pick]
            out = mc_checker.verify_many(reqs)
            for s in pick:
                got = (out.get(s) or {}).get("forced")
                good = got == truth[s]
                bad += not good
                print(f"{'OK ' if good else 'BAD'}  {s:>21}  checker {got}  expected {truth[s]}")
            mc_checker.shutdown()
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
