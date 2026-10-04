"""Broken end portal (1.16.1): the exact Minecraft check of SSA (mc_checker,
real 1.16.1 generation) must give the reference answers measured on 02/10 —
151 candidates of the fast search, 38 really broken.
Each line of broken_portal_truth_1161.json: seed -> [natural, /tp-order] broken
frames. Needs the JDK and the seed-checker project (Gradle cache).
    python tests/broken_portal_check.py [n]      (n seeds, default 8)
"""
import json, os, random, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mc_checker
import ruined_portal as rp


def main():
    ok, why = mc_checker.status()
    if not ok:
        print("SKIPPED:", why); return 0
    truth = {int(k): v for k, v in json.load(open(os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "broken_portal_truth_1161.json"))).items()}
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 8
    rng = random.Random(4)
    broken = [s for s, v in truth.items() if v[1] > 0]
    intact = [s for s, v in truth.items() if v[1] == 0]
    seeds = rng.sample(broken, n // 2) + rng.sample(intact, n - n // 2)
    # 11776766: the 1.16.1 stronghold search puts it elsewhere (bamboo jungle,
    # MC-199298) — once counted « 12 broken », 0 in game (1.16.4) (03/10)
    if 11776766 not in seeds:
        seeds.append(11776766)
    reqs = []
    for s in seeds:
        rq = rp.corrupt_request_for(s, 19)
        frames, _x, _z, rox, roz = rq
        reqs.append((s, frames, rox, roz, rq.sh))
    t = time.time()
    out = mc_checker.verify_many(reqs, natural=True)
    bad = 0
    for s in seeds:
        r = out.get(s) or {}
        got = [r.get("natural"), r.get("forced")]
        good = got == truth[s]
        bad += not good
        print(f"{'OK ' if good else 'BAD'}  {s:>12}  natural/tp  expected {truth[s]}  got {got}")
    print(f"{time.time() - t:.0f} s")
    mc_checker.shutdown()
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
