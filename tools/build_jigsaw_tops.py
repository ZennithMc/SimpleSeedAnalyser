"""
tools/build_jigsaw_tops.py — (31/10) the top view of every jigsaw template SSA draws
on the map (villages, outposts, camps): for each column (x, z) of the template, its
highest block (name, local Y), read from the Minecraft jars.

    py tools/build_jigsaw_tops.py            → loot_tables/jigsaw_tops.json.gz

Format: {"names": [block…], "tpl": {hash: [sx, sz, cells]}, "ver": {data version:
{template: hash}}}; cells: one "nnyy" hex group per column, z-major (z * sx + x),
"" for an empty column (only air / structure void / jigsaw blocks).
"""
import gzip
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
import nbt_read as nr  # noqa: E402
import jigsaw  # noqa: E402

JARS = {"1.16.5": "1.16.5-0.20.1", "1.17.1": "1.17.1-0.19.3", "1.18.2": "1.18.2-0.20.1",
        "1.19.2": "1.19.2-0.19.3", "1.19.4": "1.19.4-0.20.1", "26.2": "26.2-0.19.5",
        "26.3": "26.3-0.19.5"}
ROOT = "/mnt/user-data/uploads/versions"
SKIP = {"air", "cave_air", "void_air", "structure_void", "jigsaw", "structure_block", "barrier"}


def top_view(raw, names, idx, ymax=None, xray=False):
    """ymax (31/10): only the blocks at local Y <= ymax (a mansion floor without its
    ceiling: the « X-ray » view)."""
    d = nr.load(raw)
    pal = (d.get("palettes") or [d.get("palette")])[0]
    sx, sy, sz = d["size"]
    pn = [nr.block_state(p)["Name"].split(":")[-1] for p in pal]
    top = {}
    cols = {}
    for b in d["blocks"]:
        n = pn[b["state"]]
        if n in SKIP:
            continue
        x, y, z = b["pos"]
        if ymax is not None and y > ymax:
            continue
        if xray:
            cols.setdefault((x, z), {})[y] = n
        if (x, z) not in top or y > top[(x, z)][1]:
            top[(x, z)] = (n, y)
    if xray:                    # (05/11) the ceiling taken off: the first block under a gap
        for c, ys in cols.items():
            hs = sorted(ys, reverse=True)
            i = 0
            while i + 1 < len(hs) and hs[i + 1] == hs[i] - 1:
                i += 1
            if i + 1 < len(hs) and i < 6:
                top[c] = (ys[hs[i + 1]], hs[i + 1])
    cells = []
    for z in range(sz):
        for x in range(sx):
            t = top.get((x, z))
            if t is None:
                cells.append("")
                continue
            if t[0] not in idx:
                idx[t[0]] = len(names); names.append(t[0])
            cells.append("%02x%02x" % (idx[t[0]], t[1]))
    return [sx, sz, ",".join(cells)]


def main():
    names, idx, tpl, ver = [], {}, {}, {}
    for v, jd in JARS.items():
        z = zipfile.ZipFile(os.path.join(ROOT, jd, jd + ".jar"))
        nl = set(z.namelist())
        sdir = "data/minecraft/structure/" if any(n.startswith("data/minecraft/structure/") for n in nl) \
            else "data/minecraft/structures/"
        g = jigsaw.Gen(v)
        ver[v] = {}
        locs = list(g.T)
        if v == "26.2":                         # (31/10) bastions, ancient cities
            import bastion_loot
            import ancient_city_loot
            for name, gm in (("bastion", bastion_loot), ("ancient_city", ancient_city_loot)):
                ver[name] = {}
                for loc in gm.gen().T:
                    p = sdir + loc + ".nbt"
                    if p in nl:
                        tv = top_view(z.read(p), names, idx)
                        h = hashlib.md5(json.dumps(tv).encode()).hexdigest()[:12]
                        tpl.setdefault(h, tv)
                        ver[name][loc] = h
                print(name, len(ver[name]), flush=True)
        if v == "26.2":                         # (05/11) ruined portals, shipwrecks, ocean ruins, igloo
            for grp in ("ruined_portal", "shipwreck", "underwater_ruin", "igloo"):
                ver[grp] = {}
                for p in sorted(nl):
                    if p.startswith(sdir + grp + "/") and p.endswith(".nbt"):
                        t = p[len(sdir) + len(grp) + 1:-4]
                        tv = top_view(z.read(p), names, idx)
                        h = hashlib.md5(json.dumps(tv).encode()).hexdigest()[:12]
                        tpl.setdefault(h, tv)
                        ver[grp][t] = h
                print(grp, len(ver[grp]), flush=True)
            for grp, xr in (("trail_ruins", False), ("trial_chambers", True)):   # (05/11)
                ver[grp] = {}
                for p in sorted(nl):
                    if p.startswith(sdir + grp + "/") and p.endswith(".nbt"):
                        t = p[len(sdir):-4]
                        tv = top_view(z.read(p), names, idx, xray=xr)
                        h = hashlib.md5(json.dumps(tv).encode()).hexdigest()[:12]
                        tpl.setdefault(h, tv)
                        ver[grp][t] = h
                print(grp, len(ver[grp]), flush=True)
            p = sdir + "igloo/bottom.nbt"           # (05/11) the igloo's basement, X-ray
            ver["igloo_x"] = {"bottom": None}
            tv = top_view(z.read(p), names, idx, ymax=3)
            h = hashlib.md5(json.dumps(tv).encode()).hexdigest()[:12]
            tpl.setdefault(h, tv)
            ver["igloo_x"]["bottom"] = h
        if v == "26.2":                         # (05/11) the End cities (end_city_loot)
            import end_city_loot
            ver["end_city"] = {}
            for t in end_city_loot._NAMES:
                p = sdir + "end_city/" + t + ".nbt"
                if p in nl:
                    tv = top_view(z.read(p), names, idx)
                    h = hashlib.md5(json.dumps(tv).encode()).hexdigest()[:12]
                    tpl.setdefault(h, tv)
                    ver["end_city"][t] = h
            print("end_city", len(ver["end_city"]), flush=True)
        if v in ("1.16.5", "26.2"):             # (31/10) the woodland mansions (mansion_loot)
            name = "mansion_116" if v == "1.16.5" else "mansion_119"
            ver[name] = {}
            import mansion_loot
            for t in mansion_loot.templates(19 if v == "1.16.5" else 30):
                p = sdir + "woodland_mansion/" + t + ".nbt"
                if p in nl:
                    raw = z.read(p)
                    tv = top_view(raw, names, idx)
                    h = hashlib.md5(json.dumps(tv).encode()).hexdigest()[:12]
                    tpl.setdefault(h, tv)
                    ver[name][t] = h
                    tv = top_view(raw, names, idx, ymax=5)          # the X-ray floors
                    h = hashlib.md5(json.dumps(tv).encode()).hexdigest()[:12]
                    tpl.setdefault(h, tv)
                    ver.setdefault(name + "_x", {})[t] = h
            print(name, len(ver[name]), flush=True)
        for loc in locs:
            p = sdir + loc + ".nbt"
            if p not in nl:
                continue
            tv = top_view(z.read(p), names, idx)
            h = hashlib.md5(json.dumps(tv).encode()).hexdigest()[:12]
            tpl.setdefault(h, tv)
            ver[v][loc] = h
        print(v, len(ver[v]), "templates", len(tpl), "unique", flush=True)
    out = os.path.join(HERE, "loot_tables", "jigsaw_tops.json.gz")
    with gzip.open(out, "wt") as f:
        json.dump({"names": names, "tpl": tpl, "ver": ver}, f, separators=(",", ":"))
    print(out, os.path.getsize(out), "bytes,", len(names), "block names")


if __name__ == "__main__":
    main()
