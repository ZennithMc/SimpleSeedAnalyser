"""
ruined_118.py — ruined-portal prediction for MC 1.18+ (mc >= 22), the new
structure-start system.

Differences vs 1.13-1.17 (handled here):
  * RNG = Java LCG (LegacyRandomSource) seeded by setLargeFeatureSeed(seed,cx,cz),
    NOT the 1.16 decoration seed.
  * RNG sequence (standard overworld ruined_portal.json, 2 setups
    [underground w0.5 airpocket1.0 ; on_land_surface w0.5 airpocket0.5]):
        nextFloat -> setup (<0.5 underground else on_land_surface)
        airPocket: on_land_surface consumes 1 nextFloat (prob 0.5); underground none
        nextFloat<0.05 (giant) ; nextInt(3|10) -> template
        nextInt(4) -> rotation ; nextFloat<0.5 -> mirror (>=0.5 = FRONT_BACK)
        Y: per vertical placement (underground = random interval + terrain scan).
  * Anchor = chunk-origin (chunkX<<4, chunkZ<<4) + pivot (size/2). The footprint
    transform (_xf) already matches MC's StructureTemplate.transform exactly.

The footprint X,Z + variant are EXACT. The Y is approximate (terrain) and gets
calibrated against the 1.18.1 checker (scan_ruined_118.py).
"""
import ctypes
from stronghold_eyes import JavaRandom, get_end_portal_frames
from ruined_portal import _PSX, _PSY, _PSZ, _FOOTPRINT, _xf

_U64 = (1 << 64) - 1
RUINED_PORTAL = 11  # cubiomes structure id (overworld)


def _set_large_feature_seed(rng: JavaRandom, seed: int, cx: int, cz: int):
    """WorldgenRandom.setLargeFeatureSeed (LegacyRandomSource)."""
    rng.setSeed(seed & _U64)
    a = rng.nextLong() & _U64
    b = rng.nextLong() & _U64
    new = (((cx & _U64) * a) ^ ((cz & _U64) * b) ^ (seed & _U64)) & _U64
    rng.setSeed(new)


def ruined_variant_118(seed: int, chunk_x: int, chunk_z: int):
    """Replicate RuinedPortalStructure.findGenerationPoint up to the variant.
    Returns dict {setup, ti, rot, mirFB, footprint:set[(wx,wz)], cx, cz} — the
    EXACT footprint world columns + the variant. Y is added separately."""
    r = JavaRandom(0)
    _set_large_feature_seed(r, seed, chunk_x, chunk_z)
    # setup (2 setups, weights 0.5/0.5 ; underground first)
    f2 = r.nextFloat()
    setup = "underground" if f2 < 0.5 else "on_land_surface"
    # airPocket: sample(prob). underground prob=1.0 -> no RNG ; on_land prob=0.5
    if setup == "on_land_surface":
        r.nextFloat()  # air pocket roll (value unused for geometry)
    # template
    giant = r.nextFloat() < 0.05
    if giant:
        idx = r.nextInt_bound(3)
        ti = 11 + idx               # giant_portal_1..3 -> SSA ti 11..13
    else:
        idx = r.nextInt_bound(10)
        ti = 1 + idx                # portal_1..10 -> SSA ti 1..10
    rot = r.nextInt_bound(4)
    mirFB = (r.nextFloat() >= 0.5)  # FRONT_BACK iff nextFloat >= 0.5
    # footprint: template anchored at chunk origin, pivot = (size.x/2, size.z/2)
    px, pz = _PSX[ti] // 2, _PSZ[ti] // 2
    ox, oz = chunk_x << 4, chunk_z << 4
    fp = {_xf(lx, lz, mirFB, rot, px, pz, ox, oz) for (lx, lz) in _FOOTPRINT[ti]}
    # the portal "center" world column (pivot maps to itself) for diagnostics
    ccx, ccz = _xf(px, pz, mirFB, rot, px, pz, ox, oz)
    return {"setup": setup, "ti": ti, "rot": rot, "mirFB": mirFB,
            "footprint": fp, "cx": ccx, "cz": ccz, "rng": r}


def ruined_y_118_underground(lib, seed, ti, fp, rng):
    """Approx findSuitableY for the UNDERGROUND setup. Uses cubiomes
    approx_height at the footprint box centre. Returns the random pre-scan Y
    `var9` (the terrain column scan then moves it DOWN to a solid floor — that
    part is terrain-exact only in MC, so this is an UPPER estimate; the checker
    calibrates). minY=-64 in 1.18+."""
    xs = [p[0] for p in fp]; zs = [p[1] for p in fp]
    bx = (min(xs) + max(xs)) // 2
    bz = (min(zs) + max(zs)) // 2
    lib.setup_and_seed(22, seed)
    base = int(lib.approx_height(bx, bz)) - 1
    n2 = _PSY[ti]                 # box Y span
    n4 = -64 + 15                 # minY + 15
    hi = base - n2
    if n4 < hi:
        var9 = n4 + rng.nextInt_bound(hi - n4 + 1)
    else:
        var9 = hi
    return var9, base


def corrupt_118_at(lib, seed, bx, bz, frames):
    """CORRUPTION filter for ONE 1.18+ stronghold (frames already computed).
    Returns list of hits (frame, (ruined_cx,ruined_cz), ti, setup) where the
    ruined footprint covers a frame in a DIFFERENT chunk (the cross-chunk
    overwrite — necessary condition) and the ruined is buried (underground).
    Y exactness is left to the checker. Empreinte X,Z = exact (validée)."""
    if not frames:
        return []
    # bounding box of the frames (to find ruined whose footprint can reach them)
    fxs = [f[0] for f in frames]; fzs = [f[2] for f in frames]
    x1, x2 = min(fxs) - 20, max(fxs) + 20
    z1, z2 = min(fzs) - 20, max(fzs) + 20
    try:
        ruined = lib.find_structures_in_box(RUINED_PORTAL, x1, z1, x2, z2,
                                            verify_biome=False, max_count=16)
    except Exception:
        ruined = []
    frame_xz = {(f[0], f[2]): f for f in frames}
    hits = []
    for (rx, rz) in ruined:
        rcx, rcz = rx >> 4, rz >> 4
        pred = ruined_variant_118(seed, rcx, rcz)
        if pred["setup"] != "underground":
            continue  # surface ruined can't reach a buried end portal
        for (wx, wz) in pred["footprint"]:
            f = frame_xz.get((wx, wz))
            if f is None:
                continue
            if (wx >> 4, wz >> 4) == (rcx, rcz):
                continue  # same chunk as the ruined -> the room re-wins
            hits.append((f, (rcx, rcz), pred["ti"], pred["setup"]))
    return hits


def corrupt_118_first(lib, seed):
    """Corruption candidates at the 1st stronghold of a 1.18+ seed. Cheap gate
    (a ruined near the stronghold) avoids the ~4 ms BFS on most seeds."""
    lib.setup_and_seed(22, seed)
    sh = lib.find_strongholds(max_count=1)
    if not sh:
        return []
    bx, bz = sh[0]
    # cheap gate: any ruined within ~130 blocks of the stronghold start (the end
    # portal sits ~48 blocks out, so this box covers it). Skip the BFS otherwise.
    try:
        near = lib.find_structures_in_box(RUINED_PORTAL, bx - 130, bz - 130,
                                          bx + 130, bz + 130,
                                          verify_biome=False, max_count=8)
    except Exception:
        near = []
    if not near:
        return []
    fr = get_end_portal_frames(seed, bx, bz, 22)
    if fr is None:
        return []
    frames, ex, ez, fy = fr
    return corrupt_118_at(lib, seed, bx, bz, frames)
