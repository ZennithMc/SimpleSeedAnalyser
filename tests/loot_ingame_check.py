"""
tests/loot_ingame_check.py — chest loot checked in game by the user (oracles).
    py tests/loot_ingame_check.py
Every entry: seed, version (cubiomes mc), structure, its position, then the chests
seen in game (position → {slot: item}). Exits 1 on any mismatch.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import structure_loot as sl  # noqa: E402

ORACLES = [
    # 26.2 stronghold: caves (carvers) cross the walls of both chests' chunks
    # (151 / 3 wall cells already air) — validates terrain/cave118.c (23/10)
    dict(seed=-2605588847664851059, mc=34, key="stronghold", x=1860, z=1124, chests={
        (1866, 41, 1132): {5: "eye_armor_trim_smithing_template", 11: "bread", 16: "bread",
                           18: "ender_pearl", 21: "bread", 22: "iron_helmet"},
        (1865, 43, 1116): {19: "apple", 25: "apple", 26: "ender_pearl"},
    }),
]


def main():
    bad = 0
    for o in ORACLES:
        res = {tuple(c["pos"]): c for c in sl.chests(o["key"], o["seed"], o["mc"], o["x"], o["z"], None)}
        for pos, want in o["chests"].items():
            c = res.get(pos)
            got = {} if c is None else {i: s.item.replace("minecraft:", "")
                                       for i, s in enumerate(c["slots"]) if s is not None}
            ok = got == want
            bad += not ok
            print(("OK  " if ok else "BAD ") + f"{o['seed']} mc{o['mc']} {o['key']} {pos}"
                  + ("" if ok else f"\n     want {want}\n     got  {got}"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
