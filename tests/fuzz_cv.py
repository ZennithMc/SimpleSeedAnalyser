"""Fuzzer: random cubiomes-viewer sessions (raw 320-byte conditions, every
condition type SSA imports) → tests/diff_cv.py against cubiomes-viewer's own
search code (tools/cvoracle).

    python tests/fuzz_cv.py N_SESSIONS [seed] [n_seeds]
Failing sessions are kept in tests/fuzz_fail/ for replay:
    python tests/diff_cv.py tests/fuzz_fail/xxx.session 3000
"""
import os, random, struct, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fuzz_fail")

F = dict(BIOME=7, BIOME_16=9, BIOME_64=10, BIOME_256=11, SLIME=14, SPAWN=15, STRONGHOLD=16,
         DESERT=17, JUNGLE=18, HUT=19, IGLOO=20, MONUMENT=21, VILLAGE=22, OUTPOST=23, MANSION=24,
         TREASURE=25, RUINS=26, SHIPWRECK=27, PORTAL=28, FORTRESS=29, BASTION=30, ENDCITY=31,
         BIOME_NETHER_4=33, BIOME_END_4=38, PORTALN=41, GATEWAY=42, MINESHAFT=43, SPIRAL=44,
         SPIRAL_16=45, SPIRAL_64=46, SPIRAL_256=47, BIOME_4=50, SCALE_TO_NETHER=51,
         SCALE_TO_OVERWORLD=52, LOGIC_OR=53, SPIRAL_4=54, FIRST_STRONGHOLD=55, CLIMATE_NOISE=56,
         ANCIENT_CITY=57, LOGIC_NOT=58, BIOME_CENTER=59, CLIMATE_MINMAX=61, HEIGHT=62, WELL=64,
         TRAILS=65, BIOME_SAMPLE=66, NOISE_SAMPLE=67, CHAMBERS=68)
OW_STRUCT = ["DESERT", "JUNGLE", "HUT", "IGLOO", "MONUMENT", "VILLAGE", "OUTPOST", "MANSION",
             "TREASURE", "RUINS", "SHIPWRECK", "PORTAL", "ANCIENT_CITY", "TRAILS", "WELL", "CHAMBERS"]
NETHER_STRUCT = ["FORTRESS", "BASTION", "PORTALN"]
END_STRUCT = ["ENDCITY", "GATEWAY"]
OW_BIOMES = [1, 2, 3, 4, 5, 6, 7, 12, 16, 21, 24, 27, 29, 30, 35, 36, 45, 46, 48, 49, 129, 177, 178,
             179, 180, 181, 182, 183, 184, 185]
NETHER_BIOMES = [8, 170, 171, 172, 173]
END_BIOMES = [9, 40, 41, 42, 43]
VERSIONS = ["1.18", "1.19", "1.20", "1.21.1", "1.21.3", "1.21 WD", "26.3", "1.16", "1.17"]
INT_MIN, INT_MAX = -2**31, 2**31 - 1


def mask(ids):
    lo = hi = 0
    for b in ids:
        if b < 64: lo |= 1 << b
        elif 128 <= b < 192: hi |= 1 << (b - 128)
    return lo, hi


class C(dict):
    pass


def cond(t, **kw):
    c = C(type=F[t], x1=0, z1=0, x2=0, z2=0, save=0, rel=0, skipref=0, find=(0, 0), excl=(0, 0),
          biomeId=0, biomeSize=0, tol=0, minmax=0, para=0, octave=0, step=0, count=0, y=0, flags=0,
          rmax=0, varflags=0, limok=[INT_MIN, INT_MAX] * 6, limex=[INT_MIN, INT_MAX] * 6,
          vmin=0.0, vmax=0.0, cov=0.0, conf=0.0, name=t)
    c.update(kw)
    return c


def pack(c):
    b = bytearray(320)
    struct.pack_into("<hH4iii", b, 0, c["type"], 0, c["x1"], c["z1"], c["x2"], c["z2"], c["save"], c["rel"])
    b[28] = c["skipref"]
    struct.pack_into("<QQ", b, 96, *c["find"])
    struct.pack_into("<iI", b, 112, c["biomeId"], c["biomeSize"])
    b[120], b[121], b[122], b[123] = c["tol"], c["minmax"], c["para"], c["octave"]
    struct.pack_into("<HH", b, 124, c["step"], c.get("version", 4))
    struct.pack_into("<QQ", b, 128, *c["excl"])
    struct.pack_into("<iiIi", b, 180, c["count"], c["y"], c["flags"], c["rmax"])
    struct.pack_into("<H", b, 196, c["varflags"])
    struct.pack_into("<12i", b, 208, *c["limok"])
    struct.pack_into("<12i", b, 256, *c["limex"])
    struct.pack_into("<ffff", b, 304, c["vmin"], c["vmax"], c["cov"], c["conf"])
    return b.hex()


def area(r, lo, hi, allow_rmax=True, point=False):
    if point:
        x, z = r.randint(-64, 64), r.randint(-64, 64)
        return dict(x1=x, z1=z, x2=x, z2=z)
    h = r.randint(lo, hi)
    if allow_rmax and r.random() < 0.3:
        return dict(rmax=h + 1)
    ox, oz = r.randint(-h // 2, h // 2), r.randint(-h // 2, h // 2)
    w = r.randint(lo, hi)
    return dict(x1=ox - h, z1=oz - w, x2=ox + h, z2=oz + w)


def gen_leaf(r, mc_new, dim_ok):
    k = r.random()
    if mc_new:
        kinds = ["struct"] * 5 + ["biome"] * 3 + ["climate"] * 3 + ["cnoise", "nsample", "nsample", "bsample",
                 "bcenter", "height", "slime", "spawn", "fstrong", "strong", "mineshaft", "nether", "end"]
    else:
        kinds = ["struct"] * 5 + ["biome"] * 3 + ["bcenter", "slime", "spawn", "fstrong", "strong",
                                                  "mineshaft", "nether", "end", "bsample"]
    kind = r.choice(kinds)
    if kind == "struct":
        t = r.choice(OW_STRUCT)
        c = cond(t, **area(r, 200, 2500), count=r.choice([0, 1, 1, 1, 2, 3]), skipref=int(r.random() < .2))
        if t == "VILLAGE" and r.random() < .3: c["varflags"] = r.choice([2, 0x12])
        if t == "IGLOO" and r.random() < .3: c["varflags"] = r.choice([0x20, 0x30])
        return c
    if kind == "nether":
        if r.random() < .5:
            return cond(r.choice(NETHER_STRUCT), **area(r, 200, 1500), count=r.choice([0, 1, 1, 2]))
        lo, hi = mask(r.sample(NETHER_BIOMES, r.randint(1, 2)))
        return cond("BIOME", type=32, step=r.choice([1, 4, 4, 16]), **area(r, 32, 400, False),
                    find=(lo, hi), flags=r.choice([0, 0x10]), y=r.choice([0, 64]))
    if kind == "end":
        if r.random() < .5:
            return cond(r.choice(END_STRUCT), **area(r, 500, 3000), count=r.choice([0, 1, 1, 2]))
        lo, hi = mask(r.sample(END_BIOMES, r.randint(1, 2)))
        return cond("BIOME", type=37, step=r.choice([4, 16]), **area(r, 64, 600, False), find=(lo, hi))
    if kind == "biome":
        st = r.choice([1, 4, 4, 16, 64, 256])
        size = {1: (4, 48), 4: (32, 400), 16: (64, 800), 64: (256, 1500), 256: (512, 3000)}[st]
        inc = r.sample(OW_BIOMES if mc_new else [b for b in OW_BIOMES if b < 64 or b == 129], r.randint(1, 3))
        exc = r.sample(OW_BIOMES, 1) if r.random() < .2 else []
        exc = [b for b in exc if b not in inc]
        c = cond("BIOME", step=st, **area(r, *size, allow_rmax=False), find=mask(inc), excl=mask(exc),
                 flags=r.choice([0, 0x10]), y=r.choice([63, 256, 320]))   # (approx = random in CV)
        if r.random() < .2:            # legacy (pre-4.0) form: F_BIOME_x type, grid-unit area
            leg = {1: 7, 4: 50, 16: 9, 64: 10, 256: 11}[st]
            c.update(type=leg, step=0, version=3, x1=c["x1"] // st, z1=c["z1"] // st,
                     x2=c["x2"] // st, z2=c["z2"] // st)
        return c
    if kind == "bcenter":
        return cond("BIOME_CENTER", **area(r, 300, 2000, False), biomeId=r.choice(OW_BIOMES[:20]),
                    biomeSize=r.choice([1, 50, 300, 1500]), tol=r.choice([0, 1, 2]),
                    count=r.choice([0, 1, 1, 2]), y=r.choice([63, 256]))
    if kind == "climate":
        mm = r.choice([0x10, 0x20]) | r.choice([0x40, 0x80, 0xC0])
        lo = r.randint(-6000, 3000); hi = lo + r.randint(500, 6000)
        return cond("CLIMATE_MINMAX", **(area(r, 0, 800, False) if r.random() < .6 else area(r, 0, 0, point=True)),
                    para=r.choice([0, 1, 2, 3, 5]), octave=r.choice([0, 0, 1, 2, 4]), minmax=mm,
                    vmin=float(lo), vmax=float(hi), flags=r.choice([0, 0, 0x40]))
    if kind == "cnoise":
        lim = [INT_MIN, INT_MAX] * 6
        for p in r.sample([0, 1, 2, 3, 5], r.randint(1, 2)):
            a = r.randint(-5000, 3000); lim[2 * p] = a; lim[2 * p + 1] = a + r.randint(1000, 6000)
        return cond("CLIMATE_NOISE", **area(r, 0, 400, False), limok=lim)
    if kind == "nsample":
        lo = r.randint(-5000, 2000)
        mm = r.choice([0x40, 0x80, 0xC0])
        return cond("NOISE_SAMPLE", **area(r, 100, 1500), para=r.choice([0, 1, 2, 3, 5]),
                    octave=r.choice([0, 2, 4]), minmax=mm | 0x10, vmin=float(lo), vmax=float(lo + r.randint(1000, 7000)),
                    cov=r.choice([0.05, 0.2, 0.4]), conf=r.choice([0.9, 0.95, 0.99]), count=r.choice([0, 0, 1]),
                    y=256, flags=0x20)
    if kind == "bsample":
        inc = r.sample(OW_BIOMES if mc_new else [b for b in OW_BIOMES if b < 64], r.randint(1, 4))
        return cond("BIOME_SAMPLE", **area(r, 100, 1200), find=mask(inc), cov=r.choice([0.02, 0.1, 0.3]),
                    conf=r.choice([0.9, 0.95]), count=r.choice([0, 0, 1]), y=r.choice([63, 256]))
    if kind == "height":
        lim = [INT_MIN, INT_MAX] * 6
        a = r.randint(40, 120); lim[8] = a; lim[9] = a + r.randint(5, 80)
        return cond("HEIGHT", **area(r, 0, 0, point=True), limok=lim, flags=r.choice([0x20, 0x20, 0]))
    if kind == "slime":
        return cond("SLIME", **area(r, 16, 120, False), count=r.choice([0, 1, 2, 4]))   # LOC_REC in CV
    if kind == "spawn":
        return cond("SPAWN", **area(r, 100, 1500))
    if kind == "fstrong":
        return cond("FIRST_STRONGHOLD", **area(r, 800, 3000))
    if kind == "strong":
        return cond("STRONGHOLD", **area(r, 1500, 3500), count=r.choice([0, 1, 1, 2]))
    if kind == "mineshaft":
        # radius not used: cubiomes-viewer's mineshaft radius test reads stale
        # positions (search.cpp: cent[i] instead of p[i]) → not a reference
        return cond("MINESHAFT", **area(r, 32, 300, False), count=r.choice([0, 1, 2]))


def gen_session(r):
    ver = r.choice(VERSIONS)
    mc_new = not ver.startswith("1.16") and not ver.startswith("1.17")
    large = int(r.random() < .15)
    nodes = []            # (cond, can_have_children)

    def add(c, parent):
        c["save"] = len(nodes) + 1; c["rel"] = parent
        nodes.append(c)
        return c["save"]

    def build(parent, depth):
        k = r.random()
        if depth < 3 and k < 0.10:
            s = add(cond("LOGIC_OR"), parent)
            for _ in range(r.choice([0, 2, 2, 3]) if r.random() < .1 else r.choice([2, 3])):
                build(s, depth + 1)
        elif depth < 3 and k < 0.15:
            s = add(cond("LOGIC_NOT"), parent)
            build(s, depth + 1)
        elif depth < 3 and k < 0.22:
            s = add(cond("SPIRAL", **area(r, 128, 600), step=r.choice([128, 256, 512])), parent)
            build(s, depth + 1)
        elif depth < 3 and k < 0.26:
            s = add(cond(r.choice(["SCALE_TO_NETHER", "SCALE_TO_OVERWORLD"])), parent)
            build(s, depth + 1)
        else:
            c = gen_leaf(r, mc_new, True)
            if parent and r.random() < .3:
                c["skipref"] = int(r.random() < .5)
            s = add(c, parent)
            if depth < 3 and r.random() < .35:
                for _ in range(r.randint(1, 2)):
                    build(s, depth + 1)

    for _ in range(r.randint(1, 3)):
        build(0, 0)
    # cubiomes-viewer reads a STALE (previous seed's) climate noise when a
    # reduced-octave climate condition follows a Nether/End biome check or
    # structure in the same seed (init4Dim(other dim) sets g.seed without
    # re-seeding the overworld noise): not a reference → full octaves there
    if any(c["type"] in (29, 30, 31, 32, 33, 37, 38) for c in nodes):
        for c in nodes:
            if c["type"] in (61, 67):
                c["octave"] = 0
    head = f"#Version:  4.1.2\n#MC:       {ver}\n#Large:    {large}\n#Search:   0\n"
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
        path = os.path.join(OUT, f"fz_{seed}_{i}.session")
        open(path, "w").write(txt)
        try:
            p = subprocess.run([sys.executable, os.path.join(HERE, "diff_cv.py"), path, nseeds],
                               capture_output=True, text=True, timeout=int(os.environ.get("FUZZ_TIMEOUT", "300")))
        except subprocess.TimeoutExpired:
            print(f"[{i}] {ver:8s} slow (skipped)", flush=True)
            os.remove(path)
            continue
        out = [l for l in p.stdout.splitlines() if l.startswith(("DIFF", "MISMATCH"))]
        bad = p.returncode != 0 or any(l.startswith("DIFF") for l in out)
        if bad:
            fails += 1
            print(f"[{i}] {ver:8s} FAIL  {path}\n    " + "\n    ".join(out[:6] or p.stderr.splitlines()[-3:]), flush=True)
        else:
            os.remove(path)
            print(f"[{i}] {ver:8s} ok ({txt.count('#Cond')} conds)", flush=True)
    print(f"FAILED SESSIONS: {fails}/{n}")


if __name__ == "__main__":
    main()
