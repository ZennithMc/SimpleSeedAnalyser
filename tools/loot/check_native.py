"""
tools/loot/check_native.py — (07/11) the C loot VM (loot_native) against the Python
engine (loot_engine.fill_chest): every chest table of every version, N seeds each,
slot by slot (item, count, potion, enchantments) and the « approximate » notes.

    py tools/loot/check_native.py [N] [version,...]
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
import loot_engine as le        # noqa: E402
import loot_native as ln        # noqa: E402


def key(slots):
    return [None if s is None else (s.item, s.count, s.potion, tuple(s.ench)) for s in slots]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    vers = sys.argv[2].split(",") if len(sys.argv) > 2 else le.data_versions()
    if not ln.available():
        print("lootvm missing:", ln._LIB_ERR)
        return 2
    bad = fallback = done = 0
    for v in vers:
        for tb in sorted(le._data()["versions"][v]):
            if ln.program(v, tb) is None:
                fallback += 1
                print(f"  python  {v} {tb}")
                continue
            rng = random.Random(hash((v, tb)) & 0xFFFFFF)
            for i in range(n):
                s = rng.getrandbits(64) - (1 << 63) or 1
                a, aa = le.fill_chest(tb, s, v)
                b, ba = ln.fill_chest(tb, s, v)
                done += 1
                if key(a) != key(b) or [x for x in aa if not x.startswith("location")] != ba:
                    bad += 1
                    if bad <= 10:
                        print(f"DIFF {v} {tb} seed {s}\n  py {key(a)} {aa}\n  c  {key(b)} {ba}")
                    break
    print(f"chests {done}  tables in Python {fallback}  differences {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
