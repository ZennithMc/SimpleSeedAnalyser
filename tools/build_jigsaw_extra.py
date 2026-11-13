"""tools/build_jigsaw_extra.py — (05/11) the jigsaw data of the trail ruins and trial
chambers (build_jigsaw_data.py with their pools), from a 1.21+ jar:
    py tools/build_jigsaw_extra.py <jar>   → loot_tables/trail_ruins.json.gz, trial_chambers.json.gz"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_jigsaw_data as b  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    for key in ("trail_ruins", "trial_chambers"):
        b.PREFIXES = (key + "/",)
        b.STRUCTS = (key,)
        b.SETS = (key,)
        b.main(sys.argv[1], os.path.join(HERE, "loot_tables", key + ".json.gz"))
