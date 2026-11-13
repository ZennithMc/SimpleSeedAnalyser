"""
tools/build_jigsaw_data.py — the jigsaw data SSA needs (24/10), read from a Minecraft
jar: template pools (village/*, pillager_outpost/*) and, for every template they use,
(30/10: + abandoned_camp/*, 26.3) its size, palette count, jigsaw blocks (StructureTemplate order: y, x, z) and loot
containers.  py tools/build_jigsaw_data.py <client.jar> <out.json.gz>
"""
import gzip
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nbt_read as nr  # noqa: E402

PREFIXES = ("village/", "pillager_outpost/", "abandoned_camp/")     # (30/10) + 26.3 camps
_CAMP_BIOMES = ("bamboo_jungle", "birch_forest", "cherry_grove", "dappled_forest", "flower_forest",
                "forest", "meadow", "old_growth_birch_forest", "old_growth_pine_taiga",
                "old_growth_spruce_taiga", "pale_garden", "savanna", "snowy_taiga", "sparse_jungle",
                "swamp", "taiga", "windswept_forest", "wooded_badlands")
STRUCTS = ("village_plains", "village_desert", "village_savanna", "village_snowy", "village_taiga",
           "pillager_outpost") + tuple("abandoned_camp_" + b for b in _CAMP_BIOMES)
SETS = ("villages", "pillager_outposts", "abandoned_camp")
# blocks whose block entity is a RandomizableContainer (StructureTemplate.placeInWorld
# draws a LootTableSeed for each, loot table or not)
_RAND = {"chest", "trapped_chest", "barrel", "hopper", "dispenser", "dropper",
         "decorated_pot", "crafter"}


def _id(s):
    return s.split(":", 1)[1] if ":" in s else s


class _Reports:
    """The worldgen registries dumped by the game's data generator
    (net.minecraft.data.Main --reports): 1.18.2 / 1.19.2 keep the pools in code."""

    def __init__(self, root):
        self.root = None
        for c in (os.path.join(root, "reports", "minecraft", "worldgen"),
                  os.path.join(root, "reports", "worldgen", "minecraft", "worldgen")):
            if os.path.isdir(c):
                self.root = c

    def names(self, kind):
        base = os.path.join(self.root, kind)
        for dp, _dn, fs in os.walk(base):
            for f in fs:
                if f.endswith(".json"):
                    rel = os.path.relpath(os.path.join(dp, f), base).replace(os.sep, "/")[:-5]
                    yield rel, os.path.join(dp, f)


def main(jar, out, reports=None):
    z = zipfile.ZipFile(jar)
    names = z.namelist()
    rep = _Reports(reports) if reports else None
    sdir = "data/minecraft/structure/" if any(n.startswith("data/minecraft/structure/") for n in names) \
        else "data/minecraft/structures/"
    pools = {}
    if rep is not None:
        for key, path in rep.names("template_pool"):
            if key.startswith(PREFIXES) or key == "empty":
                pools[key] = json.load(open(path))
    for n in names:
        if n.startswith("data/minecraft/worldgen/template_pool/") and n.endswith(".json"):
            key = n[len("data/minecraft/worldgen/template_pool/"):-5]
            if key.startswith(PREFIXES) or key == "empty":
                pools[key] = json.loads(z.read(n))
    locs = set()

    def walk(el):
        t = _id(el["element_type"])
        if t in ("single_pool_element", "legacy_single_pool_element"):
            locs.add(_id(el["location"]))
        elif t == "list_pool_element":
            for e in el["elements"]:
                walk(e)
    for p in pools.values():
        for e in p["elements"]:
            walk(e["element"])
    temps = {}
    for loc in sorted(locs):
        if sdir + loc + ".nbt" not in names:          # (30/10) a template the jar lacks
            temps[loc] = dict(size=[0, 0, 0], npal=1, jig=[], cont=[], missing=True)   # (empty: getOrCreate)
            continue
        raw = z.read(sdir + loc + ".nbt")
        d = nr.load(raw)
        pals = d.get("palettes") or [d.get("palette")]
        pal = pals[0]
        jig, cont = [], []
        blocks = sorted(d["blocks"], key=lambda b: (b["pos"][1], b["pos"][0], b["pos"][2]))
        for b in blocks:
            st = nr.block_state(pal[b["state"]])
            nm = _id(st["Name"])
            if nm == "jigsaw" and "nbt" in b:
                t = b["nbt"]
                jig.append(dict(pos=b["pos"], ori=st.get("Properties", {}).get("orientation", "north_up"),
                                name=_id(t.get("name", "minecraft:empty")),
                                target=_id(t.get("target", "minecraft:empty")),
                                pool=_id(t.get("pool", "minecraft:empty")),
                                joint=t.get("joint"), pp=t.get("placement_priority", 0),
                                sp=t.get("selection_priority", 0)))
            elif "nbt" in b and (nm in _RAND or nm.endswith(("shulker_box", "copper_chest"))):
                t = b["nbt"].get("LootTable")
                cont.append(dict(pos=b["pos"], block=nm, table=_id(t) if t else None,
                                 facing=st.get("Properties", {}).get("facing")))
        temps[loc] = dict(size=d["size"], npal=len(pals), jig=jig, cont=cont)
    procs = {}
    if rep is not None:
        for key, path in rep.names("processor_list"):
            procs[key] = json.load(open(path))
    for n in names:
        if n.startswith("data/minecraft/worldgen/processor_list/") and n.endswith(".json"):
            procs[n.rsplit("/", 1)[1][:-5]] = json.loads(z.read(n))
    structs, sets, tags = {}, {}, {}
    for key in STRUCTS:
        n = f"data/minecraft/worldgen/structure/{key}.json"
        if n in names:
            structs[key] = json.loads(z.read(n))
    for key in SETS:
        n = f"data/minecraft/worldgen/structure_set/{key}.json"
        if n in names:
            sets[key] = json.loads(z.read(n))
    if rep is not None:
        for key, path in rep.names("structure"):
            if key in ("village_plains", "village_desert", "village_savanna", "village_snowy",
                       "village_taiga", "pillager_outpost"):
                structs[key] = json.load(open(path))
        for key, path in rep.names("configured_structure_feature"):       # 1.18.2
            if key in ("village_plains", "village_desert", "village_savanna", "village_snowy",
                       "village_taiga", "pillager_outpost"):
                d = json.load(open(path))
                structs[key] = {"type": "minecraft:jigsaw", "biomes": d["biomes"],
                                "max_distance_from_center": 80,
                                "project_start_to_heightmap": "WORLD_SURFACE_WG",
                                "size": d["config"]["size"], "start_height": {"absolute": 0},
                                "start_pool": d["config"]["start_pool"],
                                "terrain_adaptation": "beard_thin" if d.get("adapt_noise") else "none",
                                "use_expansion_hack": True, "legacy_1182": True}
        for key, path in rep.names("structure_set"):
            if key in ("villages", "pillager_outposts"):
                sets[key] = json.load(open(path))

    def tag(name, seen=()):
        n = f"data/minecraft/tags/worldgen/biome/{name}.json"
        if n not in names:
            return []
        out = []
        for v in json.loads(z.read(n))["values"]:
            v = v["id"] if isinstance(v, dict) else v
            if v.startswith("#"):
                out += tag(_id(v[1:]))
            else:
                out.append(_id(v))
        return out
    for key, st in structs.items():
        b = st.get("biomes", "")
        tags[key] = tag(_id(b[1:])) if isinstance(b, str) and b.startswith("#") else \
            [_id(x) for x in (b if isinstance(b, list) else [b])]
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump(dict(pools=pools, templates=temps, structures=structs, sets=sets,
                       biomes=tags, processors=procs), f, separators=(",", ":"))
    print(len(pools), "pools", len(temps), "templates")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
