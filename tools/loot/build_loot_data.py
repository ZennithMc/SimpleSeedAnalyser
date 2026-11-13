"""tools/loot/build_loot_data.py — (25/10) packs the vanilla chest loot tables of
1.16 → 26.3 into loot_tables/chests.json.gz for loot_engine.py.

Source: a folder <src>/<version>/<chest>.json per version (the game's own data,
e.g. from misode/mcmeta « <version>-data »). Identical tables are stored once.

    py tools/loot/build_loot_data.py <src>"""
import gzip, hashlib, json, os, sys

VERSIONS = ("1.16 1.16.1 1.16.2 1.16.3 1.16.4 1.16.5 1.17 1.17.1 1.18 1.18.1 1.18.2 1.19 1.19.1 "
            "1.19.2 1.19.3 1.19.4 1.20 1.20.1 1.20.2 1.20.3 1.20.4 1.20.5 1.20.6 1.21 1.21.1 1.21.2 "
            "1.21.3 1.21.4 1.21.5 1.21.6 1.21.7 1.21.8 1.21.9 1.21.10 1.21.11 26.1 26.2 26.3").split()


def main(src):
    out = {"versions": {}, "blobs": {}}
    for v in VERSIONS:
        d = os.path.join(src, v)
        if not os.path.isdir(d):
            continue
        tab = {}
        for root, _ds, fs in os.walk(d):
            for f in fs:
                if not f.endswith(".json"):
                    continue
                p = os.path.join(root, f)
                name = os.path.relpath(p, d)[:-5].replace(os.sep, "/")
                obj = json.load(open(p, encoding="utf-8"))
                txt = json.dumps(obj, sort_keys=True, separators=(",", ":"))
                h = hashlib.sha1(txt.encode()).hexdigest()[:12]
                out["blobs"].setdefault(h, obj)
                tab[name] = h
        out["versions"][v] = dict(sorted(tab.items()))
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dst = os.path.join(here, "loot_tables", "chests.json.gz")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with gzip.open(dst, "wt", encoding="utf-8") as g:
        json.dump(out, g, separators=(",", ":"))
    print(len(out["versions"]), "versions,", len(out["blobs"]), "distinct tables ->", dst,
          os.path.getsize(dst), "bytes")


def build_enchant(src):
    """(26/10) The data-driven enchantments of 1.21+ (enchantment/*.json and the
    enchantment / item tags they use), per version, into loot_tables/enchant.json.gz.
    <src>/<version>/enchantment/*.json, <src>/<version>/tags/{enchantment,item}/**.json"""
    out = {"versions": {}, "blobs": {}}
    for v in VERSIONS:
        d = os.path.join(src, v)
        if not os.path.isdir(d):
            continue
        tab = {}
        for root, _ds, fs in os.walk(d):
            for f in fs:
                if not f.endswith(".json"):
                    continue
                p = os.path.join(root, f)
                name = os.path.relpath(p, d)[:-5].replace(os.sep, "/")
                obj = json.load(open(p, encoding="utf-8"))
                txt = json.dumps(obj, sort_keys=True, separators=(",", ":"))
                h = hashlib.sha1(txt.encode()).hexdigest()[:12]
                out["blobs"].setdefault(h, obj)
                tab[name] = h
        out["versions"][v] = dict(sorted(tab.items()))
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dst = os.path.join(here, "loot_tables", "enchant.json.gz")
    with gzip.open(dst, "wt", encoding="utf-8") as g:
        json.dump(out, g, separators=(",", ":"))
    print(len(out["versions"]), "versions,", len(out["blobs"]), "distinct files ->", dst,
          os.path.getsize(dst), "bytes")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--enchant":
        build_enchant(sys.argv[2])
    else:
        main(sys.argv[1])

