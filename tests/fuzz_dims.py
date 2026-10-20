"""Fuzzer of the Nether / End conditions (18/10): random cubiomes-viewer
sessions made of Nether / End structures (with their variants: end ship,
dense fortress, bastion start pieces) and Nether / End biome filters, alone or
linked to overworld conditions through the scale helpers, OR / NOT gates and
spirals → tests/diff_cv.py against cubiomes-viewer's own search code.

    python tests/fuzz_dims.py N_SESSIONS [seed] [n_seeds]
Failing sessions are kept in tests/fuzz_fail/ (replay with diff_cv.py).
"""
import os, random, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fuzz_cv as fz                                    # noqa: E402

OUT = fz.OUT
VERSIONS = ["1.16.1", "1.16", "1.17", "1.18", "1.19", "1.20", "1.21.1", "1.21 WD", "26.3"]
BASTION_BITS = [1 << 16, 1 << 17, 1 << 18, 1 << 19]       # cubiomes-viewer g_start_pieces


def pack(c):
    h = bytearray.fromhex(fz.pack(c))
    struct.pack_into("<Q", h, 200, c.get("varstart", 0))
    return h.hex()


def nether_leaf(r):
    k = r.random()
    if k < 0.65:
        t = r.choice(fz.NETHER_STRUCT)
        c = fz.cond(t, **fz.area(r, 150, 1500), count=r.choice([0, 1, 1, 1, 2, 3]),
                    skipref=int(r.random() < .15))
        if t == "FORTRESS" and r.random() < .35:
            c["varflags"] = 0x0008                                  # VAR_DENSE_BB
            c.update(fz.area(r, 1500, 4000)); c["count"] = 1
        if t == "BASTION" and r.random() < .4:
            c["varflags"] = 0x0001                                  # VAR_WITH_START
            c["varstart"] = sum(r.sample(BASTION_BITS, r.randint(1, 3)))
        return c
    lo, hi = fz.mask(r.sample(fz.NETHER_BIOMES, r.randint(1, 3)))
    ex = (0, 0)
    if r.random() < .25:
        ex = fz.mask(r.sample([b for b in fz.NETHER_BIOMES], 1))
        if ex[0] & lo or ex[1] & hi:
            ex = (0, 0)
    st = r.choice([1, 4, 4, 16, 64, 256])
    size = {1: (8, 64), 4: (32, 400), 16: (64, 800), 64: (256, 2000), 256: (512, 4000)}[st]
    return fz.cond("BIOME", type=32, step=st, **fz.area(r, *size, allow_rmax=False),
                   find=(lo, hi), excl=ex, flags=r.choice([0, 0x10]), y=r.choice([0, 64]))


def end_leaf(r):
    k = r.random()
    if k < 0.6:
        t = r.choice(fz.END_STRUCT)
        c = fz.cond(t, **fz.area(r, 300, 3000), count=r.choice([0, 1, 1, 1, 2]),
                    skipref=int(r.random() < .15))
        if t == "ENDCITY" and r.random() < .45:
            c["varflags"] = r.choice([0x0004, 0x0014])              # ship / no ship
        if t == "GATEWAY":
            c.update(fz.area(r, 100, 900))
        return c
    lo, hi = fz.mask(r.sample(fz.END_BIOMES, r.randint(1, 2)))
    st = r.choice([1, 4, 16, 16, 64])
    size = {1: (8, 64), 4: (64, 600), 16: (128, 1500), 64: (512, 4000)}[st]
    return fz.cond("BIOME", type=37, step=st, **fz.area(r, *size, allow_rmax=False),
                   find=(lo, hi), flags=r.choice([0, 0x10]), y=r.choice([0, 64]))


def gen_session(r):
    ver = r.choice(VERSIONS)
    mc_new = not ver.startswith("1.16") and not ver.startswith("1.17")
    nodes = []

    def add(c, parent):
        c["save"] = len(nodes) + 1; c["rel"] = parent
        nodes.append(c)
        return c["save"]

    def leaf(depth):
        k = r.random()
        if k < 0.42:
            return nether_leaf(r)
        if k < 0.84:
            return end_leaf(r)
        c = fz.gen_leaf(r, mc_new, True)               # an overworld one sometimes
        return c

    # a NOT inside a NOT is left out: cubiomes-viewer answers COND_OK for a
    # « maybe » in its fast pass, so NOT(NOT(x)) rejects every seed there
    # (SSA keeps the logical answer) — not a Nether / End matter
    def build(parent, depth, under_not=False):
        k = r.random()
        if depth < 3 and k < 0.10:
            s = add(fz.cond("LOGIC_OR"), parent)
            for _ in range(r.choice([2, 3])):
                build(s, depth + 1, under_not)
        elif depth < 3 and k < 0.15 and not under_not:
            s = add(fz.cond("LOGIC_NOT"), parent)
            build(s, depth + 1, True)
        elif depth < 3 and k < 0.20:
            s = add(fz.cond("SPIRAL", **fz.area(r, 128, 600), step=r.choice([128, 256, 512])), parent)
            build(s, depth + 1, under_not)
        elif depth < 3 and k < 0.32:
            s = add(fz.cond(r.choice(["SCALE_TO_NETHER", "SCALE_TO_OVERWORLD"])), parent)
            build(s, depth + 1, under_not)
        else:
            c = leaf(depth)
            if parent and r.random() < .3:
                c["skipref"] = int(r.random() < .5)
            s = add(c, parent)
            if depth < 3 and r.random() < .4:
                for _ in range(r.randint(1, 2)):
                    build(s, depth + 1, under_not)

    for _ in range(r.randint(1, 3)):
        build(0, 0)
    # same cubiomes-viewer stale-climate caveat as fuzz_cv.py
    if any(c["type"] in (29, 30, 31, 32, 33, 37, 38, 41, 42) for c in nodes):
        for c in nodes:
            if c["type"] in (61, 67):
                c["octave"] = 0
    head = f"#Version:  4.1.2\n#MC:       {ver}\n#Large:    {int(r.random() < .1)}\n#Search:   0\n"
    return head + "".join(f"#Cond: {pack(c)}\n" for c in nodes), ver


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    nseeds = sys.argv[3] if len(sys.argv) > 3 else "1500"
    os.makedirs(OUT, exist_ok=True)
    r = random.Random(seed)
    fails = 0
    for i in range(n):
        txt, ver = gen_session(r)
        path = os.path.join(OUT, f"fd_{seed}_{i}.session")
        open(path, "w").write(txt)
        try:
            p = subprocess.run([sys.executable, os.path.join(HERE, "diff_cv.py"), path, nseeds],
                               capture_output=True, text=True,
                               timeout=int(os.environ.get("FUZZ_TIMEOUT", "300")))
        except subprocess.TimeoutExpired:
            print(f"[{i}] {ver:8s} slow (skipped)", flush=True)
            os.remove(path)
            continue
        out = [l for l in p.stdout.splitlines() if l.startswith(("DIFF", "MISMATCH"))]
        bad = p.returncode != 0 or any(l.startswith("DIFF") for l in out)
        if bad:
            fails += 1
            print(f"[{i}] {ver:8s} FAIL  {path}\n    " + "\n    ".join(out[:6] or p.stderr.splitlines()[-3:]),
                  flush=True)
        else:
            os.remove(path)
            print(f"[{i}] {ver:8s} ok ({txt.count('#Cond')} conds)", flush=True)
    print(f"FAILED SESSIONS: {fails}/{n}")


if __name__ == "__main__":
    main()
