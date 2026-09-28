"""
scan_ruined_118.py — validate the 1.18+ ruined-portal FOOTPRINT (X,Z) + find its
real Y, against the real Minecraft 1.18.1 checker.

For a seed, it finds a ruined portal, predicts its footprint+variant (exact) and
its underground Y (approx), then scans those footprint columns over a Y band and
reports where the REAL ruined blocks (obsidian/netherrack/...) are.
"""
import os
import cubiomes_wrap as cw
import checker_client
from ruined_118 import (ruined_variant_118, ruined_y_118_underground,
                        RUINED_PORTAL)

MC = 22
SEED = 1

_here = os.path.dirname(os.path.abspath(__file__))
checker_client._CHECKER_DIR = os.path.normpath(os.path.join(
    _here, "..", "Sources to relie on",
    "seed-checker-1.18.1", "seed-checker-1.18.1"))

RUINED_BLOCKS = {
    "minecraft:obsidian", "minecraft:netherrack", "minecraft:magma_block",
    "minecraft:gold_block", "minecraft:crying_obsidian", "minecraft:glowstone",
    "minecraft:lava", "minecraft:gilded_blackstone", "minecraft:blackstone",
    "minecraft:chiseled_stone_bricks", "minecraft:stone_bricks",
    "minecraft:cracked_stone_bricks", "minecraft:mossy_stone_bricks",
    "minecraft:iron_bars",
}


def find_ruined(lib, seed):
    lib.setup_and_seed(MC, seed)
    try:
        cx0, cz0 = lib.get_spawn()
    except Exception:
        cx0, cz0 = 0, 0
    try:
        pts = lib.find_structures_in_box(
            RUINED_PORTAL, cx0 - 1200, cz0 - 1200, cx0 + 1200, cz0 + 1200,
            verify_biome=False, max_count=8)
    except Exception as e:
        print("find_structures_in_box error:", e)
        pts = []
    return pts


def main():
    lib = cw.get_lib()   # singleton: has the bfb (approx_height) + everything
    pts = find_ruined(lib, SEED)
    if not pts:
        print("No ruined portal found near spawn for seed", SEED); return
    rx, rz = pts[0]
    cx, cz = rx >> 4, rz >> 4
    pred = ruined_variant_118(SEED, cx, cz)
    var9, base = ruined_y_118_underground(lib, SEED, pred["ti"],
                                          pred["footprint"], pred["rng"])
    print(f"seed {SEED}: ruined pos ({rx},{rz}) chunk ({cx},{cz})")
    print(f"  setup={pred['setup']} ti={pred['ti']} rot={pred['rot']} "
          f"mirFB={pred['mirFB']}  footprint={len(pred['footprint'])} cols")
    print(f"  predicted center=({pred['cx']},{pred['cz']})  "
          f"surface base={base}  underground var9 Y={var9}")

    # full overworld Y band (don't rely on approx_height for the band)
    ylo, yhi = -64, 90
    sent = [(x, y, z) for (x, z) in sorted(pred["footprint"])
            for y in range(ylo, yhi + 1)]
    print(f"\nScanning {len(sent)} positions (Y {ylo}..{yhi}) vs real 1.18.1...")
    res = checker_client.verify([(SEED, sent, cx, cz)], timeout=400.0)
    blk = {(x, y, z): b for (x, y, z, b) in res.get(SEED, {}).get("broken", [])}
    ruined = [(x, y, z, b) for (x, y, z), b in blk.items() if b in RUINED_BLOCKS]

    print(f"\nReal ruined blocks inside predicted footprint: {len(ruined)}")
    if ruined:
        ys = sorted({y for (x, y, z, b) in ruined})
        from collections import Counter
        kinds = Counter(b.split(":")[1] for (x, y, z, b) in ruined)
        print(f"  Y levels: {ys[0]}..{ys[-1]}   blocks: {dict(kinds)}")
        print(f"  => FOOTPRINT X,Z CONFIRMED. Real ruined Y ~{ys[0]}..{ys[-1]} "
              f"vs SSA var9={var9}")
    else:
        print("  NONE inside the footprint → X,Z prediction is OFF "
              "(or the chosen ruined doesn't generate / wrong region).")


if __name__ == "__main__":
    main()
