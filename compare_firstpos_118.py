"""
compare_firstpos_118.py — validate the 48-bit corrupted approach.

Does the biome-FREE 1st-stronghold start chunk (gaider getFirstPosFast, the ring
estimate the eye finder uses) match the biome-ACCURATE one (cubiomes
find_strongholds) often enough? If yes, a biome-free 48-bit corrupted scan is
viable (it finds those corruptions exactly; the snapped minority + the checker
cover the rest). No checker needed — pure SSA-vs-SSA comparison.
"""
import cubiomes_wrap as cw
import gaider_layouts as gl

MC = 22
N = 5000


def main():
    if gl.first_pos(1) is None:
        print("spg_first_pos missing — delete gaider_layouts.dll to recompile.")
        return
    lib = cw.get_lib()
    tested = chunk_match = frame_match = 0
    for seed in range(N):
        lib.setup_and_seed(MC, seed)
        sh = lib.find_strongholds(max_count=1)
        if not sh:
            continue
        bx, bz = sh[0]
        real = (bx >> 4, bz >> 4)              # biome-accurate chunk
        fp = gl.first_pos(seed)                # biome-free ring chunk
        if fp is None:
            continue
        tested += 1
        if fp == real:
            chunk_match += 1
        fr = gl.frames_xz(seed, real[0], real[1], pre118=False)
        fg = gl.frames_xz(seed, fp[0], fp[1], pre118=False)
        if fr and fg and set(fr[2]) == set(fg[2]):
            frame_match += 1
    if not tested:
        print("no strongholds tested"); return
    print(f"tested {tested} seeds (mc {MC}):")
    print(f"  ring chunk == real chunk : {chunk_match} "
          f"({100*chunk_match/tested:.1f}%)")
    print(f"  frames identical         : {frame_match} "
          f"({100*frame_match/tested:.1f}%)")
    print()
    print(f"=> a biome-free 48-bit scan would find ~{100*frame_match/tested:.0f}% "
          f"of corruptions exactly (the rest are snapped; the checker covers them).")


if __name__ == "__main__":
    main()
