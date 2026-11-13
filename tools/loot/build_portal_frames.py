"""
tools/loot/build_portal_frames.py — (07/11) the nether-portal frames a ruined portal
can be completed into, for ruined_portal_filter.py.

For each template (identical 1.16.1 → 26.3), every vertical rectangle (outer width
4 – 23, height 5 – 23, in a plane x = k or z = k of the template, ±2 around it) whose
non-corner border holds at least one of the template's obsidian blocks and whose
inside holds none. Its cost = border cells without obsidian (to fill) + the obsidian
that became crying obsidian (to replace: crying obsidian does not make a portal).
Only the rectangles that can be the cheapest once some obsidian turns crying are
kept: m ≤ min over all (m + border obsidian).

Output loot_tables/ruined_portal_frames.json:
  {template: {"obs": [[x, y, z], …] the template's obsidian (local),
              "rects": [[axis, k, u0, y0, W, H, m, mask], …]}}
  mask: bit i = obs[i] is on the rectangle's non-corner border.
"""
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)
import ruined_portal_loot as rpl                                     # noqa: E402


def border(u0, y0, W, H):
    out = []
    for u in range(u0 + 1, u0 + W - 1):
        out += [(u, y0), (u, y0 + H - 1)]
    for y in range(y0 + 1, y0 + H - 1):
        out += [(u0, y), (u0 + W - 1, y)]
    return out


def obsidian(tpl):
    out = []
    for e in rpl.data()["blocks"][tpl].split(";"):
        x, y, z, c = e.split(",")
        if c == "o":
            out.append((int(x), int(y), int(z)))
    return sorted(out)


def frames(tpl):
    size = rpl.data()["templates"][tpl][0]
    obs = set(obsidian(tpl))
    sx, sy, sz = size
    rects = []
    for axis in ("x", "z"):
        n, m_ = (sx, sz) if axis == "x" else (sz, sx)
        for k in range(n):
            def at(u, y):
                return ((k, y, u) if axis == "x" else (u, y, k)) in obs
            if not any(at(u, y) for u in range(m_) for y in range(sy)):
                continue
            for W in range(4, 24):
                for H in range(5, 24):
                    for u0 in range(-2, m_ + 2 - W + 1):
                        for y0 in range(-2, sy + 2 - H + 1):
                            bd = border(u0, y0, W, H)
                            have = [(u, y) for u, y in bd if at(u, y)]
                            if not have:
                                continue
                            if any(at(u, y) for u in range(u0 + 1, u0 + W - 1)
                                   for y in range(y0 + 1, y0 + H - 1)):
                                continue
                            pts = [list((k, y, u) if axis == "x" else (u, y, k)) for u, y in have]
                            rects.append([axis, k, u0, y0, W, H, len(bd) - len(have), pts])
    if not rects:
        return []
    bound = min(r[6] + len(r[7]) for r in rects)
    keep = [r for r in rects if r[6] <= bound]
    keep.sort(key=lambda r: (r[6], -len(r[7]), r[0], r[1], r[2], r[3], r[4], r[5]))
    return keep


def main():
    out = {}
    for tpl in sorted(rpl.data()["templates"]):
        fr = frames(tpl)
        idx = {p: i for i, p in enumerate(obsidian(tpl))}
        out[tpl] = {"obs": [list(p) for p in obsidian(tpl)],
                    "rects": [r[:7] + [sum(1 << idx[tuple(q)] for q in r[7])] for r in fr]}
        best = fr[0] if fr else None
        print(f"{tpl:16s} {len(fr):4d} frames; best: missing {best[6]}, "
              f"{len(best[7])} obsidian, {best[4]}×{best[5]} in {best[0]}={best[1]}" if best else tpl)
    p = os.path.join(ROOT, "loot_tables", "ruined_portal_frames.json")
    with open(p, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("→", p, os.path.getsize(p), "bytes")


if __name__ == "__main__":
    main()
