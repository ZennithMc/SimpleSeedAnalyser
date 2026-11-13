"""tools/servercheck/anvil.py — read one chunk's NBT from a world's region files."""
import os, struct, sys, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import nbt_read as nr


def _region_dir(world, dim):
    d = os.path.join(world, dim, "region")
    if dim in ("DIM1", "DIM-1") and not os.path.isdir(d):     # 26.1+
        d2 = os.path.join(world, "dimensions", "minecraft",
                          "the_end" if dim == "DIM1" else "the_nether", "region")
        if os.path.isdir(d2):
            return d2
    if dim == "" and not os.path.isdir(d):          # 26.1+: dimensions/minecraft/overworld
        d2 = os.path.join(world, "dimensions", "minecraft", "overworld", "region")
        if os.path.isdir(d2):
            return d2
    return d


def chunk(world, cx, cz, dim=""):
    p = os.path.join(_region_dir(world, dim), f"r.{cx >> 5}.{cz >> 5}.mca")
    if not os.path.exists(p):
        return None
    with open(p, "rb") as f:
        f.seek(4 * ((cx & 31) + (cz & 31) * 32))
        b4 = f.read(4)
        if len(b4) < 4:
            return None
        loc = struct.unpack(">I", b4)[0]
        if loc == 0:
            return None
        f.seek((loc >> 8) * 4096)
        n, c = struct.unpack(">IB", f.read(5))
        d = f.read(n - 1)
    d = zlib.decompress(d) if c == 2 else d
    return nr._rd(d, 3 + struct.unpack_from(">H", d, 1)[0], d[0])[0]


def starts(world, cx, cz, dim=""):
    c = chunk(world, cx, cz, dim)
    if c is None:
        return None
    lv = c.get("Level", c)
    st = lv.get("Structures", lv.get("structures", {}))
    return st.get("Starts", st.get("starts", {})), lv.get("Status", lv.get("status"))


def _sections(c):
    lv = c.get("Level", c)
    for s in lv.get("sections", lv.get("Sections", [])):
        bs = s.get("block_states")
        if bs is not None:                      # 1.18+
            pal, data = bs.get("palette", []), bs.get("data")
        else:                                   # 1.16 / 1.17
            pal, data = s.get("Palette"), s.get("BlockStates")
        if not pal:
            continue
        yield s["Y"], pal, data


def _name(st):
    import nbt_read as _nr
    d = _nr.block_state(st)
    return d.get("Name", ""), d.get("Properties", {})


def blocks(c, want):
    """[(x, y, z, name, props)] of the chunk's blocks whose name ends with one of
    `want` (absolute coordinates)."""
    lv = c.get("Level", c)
    cx, cz = lv.get("xPos"), lv.get("zPos")
    out = []
    for sy, pal, data in _sections(c):
        names = [_name(p) for p in pal]
        hit = [i for i, (n, _p) in enumerate(names) if any(n.endswith(w) for w in want)]
        if not hit:
            continue
        if len(pal) == 1 or not data:
            idx = [0] * 4096
        else:
            bits = max(4, (len(pal) - 1).bit_length())
            per = 64 // bits
            mask = (1 << bits) - 1
            idx = []
            for v in data:
                v &= (1 << 64) - 1
                for k in range(per):
                    idx.append((v >> (k * bits)) & mask)
        hs = set(hit)
        for i in range(4096):
            if idx[i] in hs:
                n, p = names[idx[i]]
                out.append(((cx << 4) + (i & 15), sy * 16 + (i >> 8), (cz << 4) + ((i >> 4) & 15), n, p))
    return out


def status(c):
    if c is None:
        return None
    lv = c.get("Level", c)
    return str(lv.get("Status", lv.get("status", ""))).split(":")[-1]


def block_entities(c):
    lv = c.get("Level", c)
    return lv.get("block_entities", lv.get("TileEntities", []))


def entities(world, cx, cz):
    """The entities saved for chunk (cx, cz): entities/ region files (1.17+), or
    the chunk's own Level.Entities (1.16)."""
    for d in (os.path.join(world, "entities"),
              os.path.join(world, "dimensions", "minecraft", "overworld", "entities")):
        p = os.path.join(d, f"r.{cx >> 5}.{cz >> 5}.mca")
        if os.path.exists(p):
            with open(p, "rb") as f:
                f.seek(4 * ((cx & 31) + (cz & 31) * 32))
                b4 = f.read(4)
                if len(b4) < 4:
                    return []
                loc = struct.unpack(">I", b4)[0]
                if loc == 0:
                    return []
                f.seek((loc >> 8) * 4096)
                n, c = struct.unpack(">IB", f.read(5))
                d_ = f.read(n - 1)
            d_ = zlib.decompress(d_) if c == 2 else d_
            v = nr._rd(d_, 3 + struct.unpack_from(">H", d_, 1)[0], d_[0])[0]
            return v.get("Entities", [])
    c = chunk(world, cx, cz)
    if c is None:
        return []
    return c.get("Level", c).get("Entities", [])
