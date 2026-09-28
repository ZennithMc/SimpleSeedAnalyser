"""Print the conditions of a cubiomes-viewer .session (raw fields)."""
import struct, sys
NAMES = {7:"BIOME",9:"BIOME_16",10:"BIOME_64",11:"BIOME_256",14:"SLIME",15:"SPAWN",16:"STRONGHOLD",17:"DESERT",18:"JUNGLE",19:"HUT",20:"IGLOO",21:"MONUMENT",22:"VILLAGE",23:"OUTPOST",24:"MANSION",25:"TREASURE",26:"RUINS",27:"SHIPWRECK",28:"PORTAL",29:"FORTRESS",30:"BASTION",31:"ENDCITY",33:"BIOME_NETHER_4",38:"BIOME_END_4",41:"PORTALN",42:"GATEWAY",43:"MINESHAFT",44:"SPIRAL",45:"SPIRAL_16",46:"SPIRAL_64",47:"SPIRAL_256",50:"BIOME_4",51:"TO_NETHER",52:"TO_OW",53:"OR",54:"SPIRAL_4",55:"FIRST_SH",56:"CLIMATE_NOISE",57:"ANCIENT_CITY",58:"NOT",59:"BIOME_CENTER",61:"CLIMATE_MINMAX",62:"HEIGHT",64:"WELL",65:"TRAILS",66:"BIOME_SAMPLE",67:"NOISE_SAMPLE",68:"CHAMBERS"}
for l in open(sys.argv[1]):
    if l.startswith("#MC") or l.startswith("#Large"): print(l.strip())
    if not l.startswith("#Cond:"): continue
    b = bytes.fromhex(l.split()[1])
    t, meta, x1, z1, x2, z2, save, rel = struct.unpack_from("<hH4iii", b, 0)
    f, fm = struct.unpack_from("<QQ", b, 96); bid, bsz = struct.unpack_from("<iI", b, 112)
    tol, mm, para, octv = b[120:124]; step, = struct.unpack_from("<H", b, 124)
    ex, exm = struct.unpack_from("<QQ", b, 128)
    cnt, y, flags, rmax = struct.unpack_from("<iiIi", b, 180); vf, = struct.unpack_from("<H", b, 196)
    lim = struct.unpack_from("<12i", b, 208); vmin, vmax, cov, conf = struct.unpack_from("<ffff", b, 304)
    ids = [i for i in range(64) if f >> i & 1] + [128 + i for i in range(64) if fm >> i & 1]
    exs = [i for i in range(64) if ex >> i & 1] + [128 + i for i in range(64) if exm >> i & 1]
    s = f"[{save:2d}] <-{rel:2d} {str(NAMES.get(t, t)):14s} box=({x1},{z1},{x2},{z2}) rmax={rmax} cnt={cnt} skip={b[28]}"
    if ids or exs: s += f" incl={ids} excl={exs} flags={flags:#x} y={y}"
    if t in (59,): s += f" id={bid} size={bsz} tol={tol} y={y}"
    if t in (61, 67): s += f" para={para} oct={octv} mm={mm:#x} v=[{vmin},{vmax}] flags={flags:#x}"
    if t in (66, 67): s += f" cov={cov:.3f} conf={conf:.3f}"
    if t in (44,): s += f" step={step}"
    if t in (56, 62): s += f" lim={[l for l in lim if l not in (-2**31, 2**31-1)]} flags={flags:#x}"
    if vf: s += f" var={vf:#x}"
    print(s)
