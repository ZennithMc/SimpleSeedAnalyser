"""Top Y 1.16 by 48-bit families (topy_family) == the seed-by-seed native search,
on whole families that contain a tall chunk (03/10). ~20 s per family checked.

  py tests/topy_family_check.py [families=2] [threshold=58]
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stronghold_eyes as se  # noqa: E402
import topy_family as tf  # noqa: E402


def main():
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    thr = int(sys.argv[2]) if len(sys.argv) > 2 else 58
    tf._winit(20)
    f, done, bad = 1000, 0, 0
    while done < want:
        if tf._tall_chunks(f, thr):
            seeds = ((np.arange(65536, dtype=np.uint64) << np.uint64(48)) | np.uint64(f)
                     ).view(np.int64).tolist()
            brute = {h[0] for h in se._seed_list_maxy_native_worker((seeds, 0, thr, 20))}
            fam = {h[0] for h in tf.family_hits(f, thr)}
            ok = brute == fam
            bad += not ok
            print(f"{'OK ' if ok else 'BAD'}  family {f}: {len(brute)} seed(s) ≥ {thr}")
            done += 1
        f += 1
    print("FAILURES:", bad)
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
