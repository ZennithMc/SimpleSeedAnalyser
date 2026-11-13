"""tools/servercheck/piececat.py — (05/11) the real blocks of every stronghold and Nether
fortress piece, read from a headless server's world (mkserver.py), stored per piece
type (+ variant) in the piece's own frame (width, height, depth):
loot_tables/piece_blocks.json.gz, for the map's plans (jigsaw_plan).

    python3 piececat.py <server dir> <mc> [--no-gen] [--sh N] [--fort N]

Each cell keeps the block seen most often over the instances of that piece (caves,
lava lakes and the terrain around a bridge vary, the structure does not); the
terrain's blocks are not structure: they count as air."""
import collections, gzip, json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
R = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, R)
import numpy as np
import anvil, srv, struct_info, cubiomes_wrap as cw, fortress_loot as fl, stronghold_eyes as se

STRUCT = {
    "sh": ("stone_brick", "stone_bricks", "mossy_stone_brick", "cracked_stone_bricks", "infested_",
           "cobblestone", "iron_bars", "oak_door", "iron_door", "stone_button", "torch", "wall_torch",
           "bookshelf", "oak_planks", "oak_fence", "ladder", "cobweb", "chest", "spawner",
           "end_portal_frame", "lava", "water", "smooth_stone", "stone_slab", "stone_brick_slab",
           "stone_brick_stairs", "cobblestone_stairs", "oak_stairs", "cobblestone_slab"),
    "fort": ("nether_brick", "nether_bricks", "nether_brick_fence", "nether_brick_stairs",
             "soul_sand", "nether_wart", "lava", "spawner", "chest"),
}


def _sec_blocks(sec):
    """(Y, names[], idx[4096]) of a chunk section."""
    sy, pal, data = sec
    names = [anvil._name(p)[0].split(":")[-1] for p in pal]
    if len(pal) == 1 or data is None or len(data) == 0:
        return sy, names, np.zeros(4096, np.int64)
    bits = max(4, (len(pal) - 1).bit_length())
    per = 64 // bits
    a = np.array([int(v) & ((1 << 64) - 1) for v in data], dtype=np.uint64)
    out = np.zeros((len(a), per), np.uint64)
    for k in range(per):
        out[:, k] = (a >> np.uint64(k * bits)) & np.uint64((1 << bits) - 1)
    return sy, names, out.reshape(-1)[:4096].astype(np.int64)


class World:
    def __init__(self, w, dim):
        self.w, self.dim, self.cache = w, dim, {}

    def chunk(self, cx, cz):
        k = (cx, cz)
        if k not in self.cache:
            c = anvil.chunk(self.w, cx, cz, self.dim)
            secs = {}
            if c is not None and anvil.status(c) == "full":
                for s in anvil._sections(c):
                    sy, names, idx = _sec_blocks(s)
                    secs[sy] = (names, idx)
            else:
                secs = None
            if len(self.cache) > 400:
                self.cache.clear()
            self.cache[k] = secs
        return self.cache[k]

    def block(self, x, y, z):
        s = self.chunk(x >> 4, z >> 4)
        if s is None:
            return None
        sec = s.get(y >> 4)
        if sec is None:
            return "air"
        names, idx = sec
        return names[idx[((y & 15) << 8) | ((z & 15) << 4) | (x & 15)]]


def _keep(n, kind):
    if n is None:
        return None
    if n in ("air", "cave_air", "void_air"):
        return "air"
    return n if any(n == s or n.startswith(s) or n.endswith(s) for s in STRUCT[kind]) else "air"


def _gen(S, chunks, dim, log):
    pre = {"": "", "DIM-1": "execute in minecraft:the_nether run "}[dim]
    W = S.world
    done = lambda c: anvil.status(anvil.chunk(W, c[0], c[1], dim)) == "full"
    todo = [c for c in sorted(set(chunks)) if not done(c)]
    log(f"  {len(todo)} chunks to generate")
    t0 = time.time()
    while todo and time.time() - t0 < 5400:
        part = todo[:48]
        S.cmd(*[f"{pre}forceload add {cx * 16} {cz * 16}" for cx, cz in part])
        for _ in range(80):
            time.sleep(4)
            S.save()
            if all(done(c) for c in part):
                break
        S.cmd(f"{pre}forceload remove all")
        todo = [c for c in todo if not done(c)]
        log(f"  {len(todo)} left")


def sh_pieces(seed, mc, x, z):
    gen, rng = se._generate_full_bfs(seed, x >> 4, z >> 4, pre118=se._use_old_filler(mc))
    sea, miny = se._sea_min_for_mc(mc)
    dy = se._move_below_sea_level(gen, rng, sea, miny, 10, se._inflate_for_mc(mc))
    return gen.pieces, dy


def sh_key(p, first):
    b = p.bbox
    t = p.type
    if first:
        return "sh_start"
    if t == se.LIBRARY:
        return "sh_%d_%d" % (t, 1 if b.maxY - b.minY > 6 else 0)
    if t in (se.ROOM_CROSSING, se.STRAIGHT, se.FIVE_CROSSING):
        return "sh_%d_%d" % (t, p.additionalData)
    if t == se.FILLER_CORRIDOR:
        ns = p.orientation in (se.NORTH, se.SOUTH)
        return "sh_%d_%d" % (t, (b.maxZ - b.minZ + 1) if ns else (b.maxX - b.minX + 1))
    return "sh_%d" % t


def sh_dims(p):
    b = p.bbox
    ns = p.orientation in (se.NORTH, se.SOUTH)
    return ((b.maxX - b.minX + 1) if ns else (b.maxZ - b.minZ + 1), b.maxY - b.minY + 1,
            (b.maxZ - b.minZ + 1) if ns else (b.maxX - b.minX + 1))


def fort_dims(q):
    (x0, y0, z0), (x1, y1, z1) = q.b0, q.b1
    ns = q.rot in (0, 2)
    return ((x1 - x0 + 1) if ns else (z1 - z0 + 1), y1 - y0 + 1, (z1 - z0 + 1) if ns else (x1 - x0 + 1))


def main():
    d, mc = sys.argv[1], int(sys.argv[2])
    a = sys.argv
    nsh = int(a[a.index("--sh") + 1]) if "--sh" in a else 3
    nft = int(a[a.index("--fort") + 1]) if "--fort" in a else 8
    gen = "--no-gen" not in a
    seed = 12345
    log = lambda *m: print(*m, flush=True)
    S = srv.Server(d)
    # the strongholds
    lib = struct_info._lib(seed, mc, None, 0)
    shs = lib.find_strongholds(max_count=max(1, nsh))[:nsh]
    shp = [(x, z) + sh_pieces(seed, mc, x, z) for x, z in shs]
    ch = set()
    for _x, _z, ps, _dy in shp:
        for p in ps:
            b = p.bbox
            for cx in range(b.minX >> 4, (b.maxX >> 4) + 1):
                for cz in range(b.minZ >> 4, (b.maxZ >> 4) + 1):
                    ch.add((cx, cz))
    # the fortresses
    libn = struct_info._lib(seed, mc, None, -1)
    fts = sorted(libn.find_structures_in_box(cw.Fortress, -3000, -3000, 3000, 3000, verify_biome=True,
                                             max_count=1000), key=lambda p: p[0] ** 2 + p[1] ** 2)[:nft]
    ftp = [(x, z, fl.pieces(seed, mc, x >> 4, z >> 4)) for x, z in fts]
    chn = set()
    for _x, _z, ps in ftp:
        for q in ps:
            (x0, _y0, z0), (x1, _y1, z1) = q.b0, q.b1
            for cx in range(min(x0, x1) >> 4, (max(x0, x1) >> 4) + 1):
                for cz in range(min(z0, z1) >> 4, (max(z0, z1) >> 4) + 1):
                    chn.add((cx, cz))
    log("strongholds", shs, "chunks", len(ch), "| fortresses", fts, "chunks", len(chn))
    if gen:
        S.start()
        _gen(S, ch, "", log)
        _gen(S, chn, "DIM-1", log)
        S.save()
        S.stop()
    votes = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    dims = {}
    n_inst = collections.Counter()
    Wo = World(S.world, "")
    for _x, _z, ps, dy in shp:
        for i, p in enumerate(ps):
            k = sh_key(p, i == 0)
            W_, H_, D_ = sh_dims(p)
            dims[k] = (W_, H_, D_)
            n_inst[k] += 1
            for ld in range(D_):
                for lw in range(W_):
                    X, Z = p.get_world_x(lw, ld), p.get_world_z(lw, ld)
                    for h in range(H_):
                        n = _keep(Wo.block(X, p.bbox.minY + dy + h, Z), "sh")
                        if n is not None:
                            votes[k][(lw, h, ld)][n] += 1
    Wn = World(S.world, "DIM-1")
    for _x, _z, ps in ftp:
        for q in ps:
            k = "fort_%d" % q.typ
            W_, H_, D_ = fort_dims(q)
            dims[k] = (W_, H_, D_)
            n_inst[k] += 1
            for ld in range(D_):
                for lw in range(W_):
                    for h in range(H_):
                        X, Y, Z = q.world(lw, h, ld)
                        n = _keep(Wn.block(X, Y, Z), "fort")
                        if n is not None:
                            votes[k][(lw, h, ld)][n] += 1
    names, idx, out = [], {}, {}
    for k, cells in votes.items():
        W_, H_, D_ = dims[k]
        g = []
        for h in range(H_):
            for ld in range(D_):
                for lw in range(W_):
                    c = cells.get((lw, h, ld))
                    n = c.most_common(1)[0][0] if c else "air"
                    if n not in idx:
                        idx[n] = len(names); names.append(n)
                    g.append(idx[n])
        out[k] = dict(size=[W_, H_, D_], n=n_inst[k], cells=g)
    log({k: (v["size"], v["n"]) for k, v in sorted(out.items())})
    p = os.path.join(R, "loot_tables", "piece_blocks.json.gz")
    with gzip.open(p, "wt") as f:
        json.dump({"mc": mc, "names": names, "pieces": out}, f, separators=(",", ":"))
    log(p, os.path.getsize(p), "bytes,", len(names), "names")


if __name__ == "__main__":
    main()
