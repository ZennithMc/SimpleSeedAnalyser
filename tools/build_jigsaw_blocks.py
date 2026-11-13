"""
tools/build_jigsaw_blocks.py — every block of the village / outpost templates (25/10),
for village_world.py: {template: {"pal": [state strings], "b": [x, y, z, p, ...]}}.
Jigsaw blocks are stored as their final_state; structure blocks are left out (the
game's BlockIgnoreProcessor.STRUCTURE_BLOCK).
    py tools/build_jigsaw_blocks.py <client.jar> <jigsaw_x.json.gz> <out.json.gz>
"""
import gzip
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nbt_read as nr  # noqa: E402


def _state(st):
    n = st["Name"]
    n = n.split(":", 1)[1] if n.startswith("minecraft:") else n
    p = st.get("Properties")
    if p:
        n += "[" + ",".join(f"{k}={v}" for k, v in sorted(p.items())) + "]"
    return n


def main(jar, jdata, out):
    z = zipfile.ZipFile(jar)
    names = set(z.namelist())
    sdir = "data/minecraft/structure/" if any(n.startswith("data/minecraft/structure/") for n in names) \
        else "data/minecraft/structures/"
    with gzip.open(jdata, "rt") as f:
        locs = sorted(json.load(f)["templates"])
    res = {}
    for loc in locs:
        d = nr.load(z.read(sdir + loc + ".nbt"))
        pal = (d.get("palettes") or [d.get("palette")])[0]
        spal, idx, flat = [], {}, []
        for b in d["blocks"]:
            st = nr.block_state(pal[b["state"]])
            nm = st["Name"].split(":", 1)[-1]
            if nm == "structure_block":
                continue
            if nm == "jigsaw":
                s = b.get("nbt", {}).get("final_state", "minecraft:air")
                s = s.split(":", 1)[1] if s.startswith("minecraft:") else s
            else:
                s = _state(st)
            if s not in idx:
                idx[s] = len(spal)
                spal.append(s)
            flat += [b["pos"][0], b["pos"][1], b["pos"][2], idx[s]]
        res[loc] = dict(pal=spal, b=flat)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(res, f, separators=(",", ":"))


if __name__ == "__main__":
    main(*sys.argv[1:4])
