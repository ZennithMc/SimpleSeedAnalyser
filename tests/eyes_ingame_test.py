"""Eye counts against in-game observations (end portal frames seen in game).

    python tests/eyes_ingame_test.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cubiomes_wrap as cw          # noqa: E402
import stronghold_eyes as se        # noqa: E402

# (mc, seed, [(portal x, portal z, eyes seen in game), …])
CASES = [
    # 1.19.2, observed by Édouard 27/09 (only index 8 = +40008 fits all 8)
    (23, -4770284401290009883, [(-11529, -8466, 0), (4095, -7395, 2), (-13062, 11141, 1),
                                (19590, 3769, 0), (-3457, 17001, 1), (18479, -10306, 1),
                                (-20020, -272, 1), (-4639, -17565, 1)]),
]


def main():
    bad = 0
    for mc, seed, obs in CASES:
        lib = cw.CubiomesLib()
        lib.setup_and_seed(mc, seed)
        shs = lib.find_strongholds(max_count=128)
        for x, z, want in obs:
            sx, sz = min(shs, key=lambda p: (p[0] - x) ** 2 + (p[1] - z) ** 2)
            got = se.count_stronghold_eyes(seed, sx, sz, mc)
            ok = got == want
            bad += not ok
            print(f"{'OK  ' if ok else 'FAIL'} mc {mc} seed {seed} portal {x},{z}: {got} (in game {want})")
    print("FAILURES:", bad)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
