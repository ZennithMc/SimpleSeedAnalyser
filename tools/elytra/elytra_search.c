/*
 * elytra_search.c — End ship elytra that lands on the End spawn platform (26.x, 1.18+).
 *
 * The chain (see SSA_elytra_spawn.md, verified in the 26.3 jar):
 *   city B's piece (mostly its ship: overwrite=true, a 13x24x29 box of air) is placed
 *   AFTER city A's ship in a chunk and carves A's bow room; a return gateway
 *   (rarity 1/700, heightmap MOTION_BLOCKING + 3..9) then generates AT THE FRAME LEVEL in
 *   a carved column next to the elytra frame: its box sets the frame's support to air
 *   (the frame drops the elytra) and the item is in / next to the portal block
 *   -> teleported to the exact exit (100, 50, 0) = the spawn platform.
 *
 * Positions and pieces of End cities depend on the lower 48 bits of the seed only;
 * the city Y (ground), viability (biome, terrain) and gateways need the full seed:
 *   stage A: lower 48 bits L — every region city A with a ship, its 8 neighbours B:
 *            pieces of B near A's frame (8-chunk reference rule, LongOpenHashSet order
 *            of the two starts in each chunk), for every ground difference dY:
 *            the gateway "slots" (column, required first-free height) that would work.
 *   stage B: the 65536 upper bits: gateway of the slot chunks (cheap), then k (3..9),
 *            the biome, both grounds, terrain, viability.
 *
 * build (Windows, from the SSA folder, same gcc as build_native.py):
 *   gcc -O3 -march=native -std=gnu11 -Icubiomes_src tools/elytra/elytra_search.c
 *       cubiomes_src/finders.c cubiomes_src/generator.c cubiomes_src/biomenoise.c
 *       cubiomes_src/biomes.c cubiomes_src/layers.c cubiomes_src/noise.c
 *       cubiomes_src/util.c cubiomes_src/quadbase.c -lm -lpthread -o elytra_search.exe
 *
 * usage:
 *   elytra_search search L0 COUNT RADIUS THREADS   lower-48 seeds [L0, L0+COUNT), elytra
 *                                                  within RADIUS blocks of 0,0 -> FOUND lines
 *   elytra_search cut SEED RADIUS                  ships cut by the 8-chunk structure limit
 *                                                  whose elytra frame drops (in-game test)
 *   elytra_search pops L0 COUNT RADIUS THREADS     frames carved by a 2nd city (in-game test)
 *   elytra_search slots L0 COUNT RADIUS THREADS    (debug) the stage-A slots
 * FOUND kind: same  = the portal block is the frame's cell (the elytra spawns in it),
 *             ortho = the portal is next to the frame (the elytra must slide in),
 *             diag  = diagonal (unlikely to enter).
 * xc: 1/2 = the support / frame cell is in another chunk than the gateway: the result
 *     then depends on the chunk generation order (not deterministic); 0 = deterministic.
 * output lines on stdout (flushed), progress on stderr.
 */
#include "finders.h"
#include "generator.h"
#include "biomenoise.h"
#include "rng.h"
#include "elytra_tpl.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <stdatomic.h>

#define MC MC_26_1
#define DYMAX 48             /* ground difference B - A explored */
#define YLO (-80)            /* column cells relative to A's ground */
#define YN  320

/* the game's End city ground (copied from SSA session_engine.c, 18/10) */
void sampleNoiseColumnEnd(double column[], const SurfaceNoise *sn, const EndNoise *en,
                          int x, int z, int colymin, int colymax);
/* (biomenoise.c, not in its header) — declared here: newer GCC (w64devkit)
 * makes an implicit declaration an error */
int getSurfaceHeight(const double ncol00[], const double ncol01[],
        const double ncol10[], const double ncol11[],
        int colymin, int colymax, int blockspercell, double dx, double dz);
#define ssa_snce sampleNoiseColumnEnd
/* (18/10) isViableEndCityTerrain's corner heights over the FULL height range:
 * cubiomes stops at y 71 (cells 15-18: enough for its ≥ 60 test), the game
 * takes the real ground, up to y 127 (islands above 71 put the city higher) */
static int city_ground(const Generator *g, const SurfaceNoise *sn,
        int blockX, int blockZ)
{
    const EndNoise *en = &g->en;
    int chunkX = blockX >> 4;
    int chunkZ = blockZ >> 4;
    blockX = chunkX * 16 + 7;
    blockZ = chunkZ * 16 + 7;
    int cellx = (blockX >> 3);
    int cellz = (blockZ >> 3);

    enum { y0 = 15, y1 = 32, yn = y1-y0+1 };
    double ncol[3][3][yn];

    ssa_snce(ncol[0][0], sn, en, cellx, cellz, y0, y1);
    ssa_snce(ncol[0][1], sn, en, cellx, cellz+1, y0, y1);
    ssa_snce(ncol[1][0], sn, en, cellx+1, cellz, y0, y1);
    ssa_snce(ncol[1][1], sn, en, cellx+1, cellz+1, y0, y1);

    int h00, h01, h10, h11;
    h00 = getSurfaceHeight(ncol[0][0], ncol[0][1], ncol[1][0], ncol[1][1],
            y0, y1, 4, (blockX & 7) / 8.0, (blockZ & 7) / 8.0);

    uint64_t cs;
    if (en->mc <= MC_1_18)
        setSeed(&cs, chunkX + chunkZ * 10387313ULL);
    else
        cs = chunkGenerateRnd(g->seed, chunkX, chunkZ);

    switch (nextInt(&cs, 4))
    {
    case 0: // (++) 0
        ssa_snce(ncol[0][2], sn, en, cellx+0, cellz+2, y0, y1);
        ssa_snce(ncol[1][2], sn, en, cellx+1, cellz+2, y0, y1);
        ssa_snce(ncol[2][0], sn, en, cellx+2, cellz+0, y0, y1);
        ssa_snce(ncol[2][1], sn, en, cellx+2, cellz+1, y0, y1);
        ssa_snce(ncol[2][2], sn, en, cellx+2, cellz+2, y0, y1);
        h01 = getSurfaceHeight(ncol[0][1], ncol[0][2], ncol[1][1], ncol[1][2],
                y0, y1, 4, ((blockX    ) & 7) / 8.0, ((blockZ + 5) & 7) / 8.0);
        h10 = getSurfaceHeight(ncol[1][0], ncol[1][1], ncol[2][0], ncol[2][1],
                y0, y1, 4, ((blockX + 5) & 7) / 8.0, ((blockZ    ) & 7) / 8.0);
        h11 = getSurfaceHeight(ncol[1][1], ncol[1][2], ncol[2][1], ncol[2][2],
                y0, y1, 4, ((blockX + 5) & 7) / 8.0, ((blockZ + 5) & 7) / 8.0);
        break;

    case 1: // (-+) 90
        ssa_snce(ncol[0][2], sn, en, cellx+0, cellz+2, y0, y1);
        ssa_snce(ncol[1][2], sn, en, cellx+1, cellz+2, y0, y1);
        h01 = getSurfaceHeight(ncol[0][1], ncol[0][2], ncol[1][1], ncol[1][2],
                y0, y1, 4, ((blockX    ) & 7) / 8.0, ((blockZ + 5) & 7) / 8.0);
        h10 = getSurfaceHeight(ncol[0][0], ncol[0][1], ncol[1][0], ncol[1][1],
                y0, y1, 4, ((blockX - 5) & 7) / 8.0, ((blockZ    ) & 7) / 8.0);
        h11 = getSurfaceHeight(ncol[0][1], ncol[0][2], ncol[1][1], ncol[1][2],
                y0, y1, 4, ((blockX - 5) & 7) / 8.0, ((blockZ + 5) & 7) / 8.0);
        break;

    case 2: // (--) 180
        h01 = getSurfaceHeight(ncol[0][0], ncol[0][1], ncol[1][0], ncol[1][1],
                y0, y1, 4, ((blockX    ) & 7) / 8.0, ((blockZ - 5) & 7) / 8.0);
        h10 = getSurfaceHeight(ncol[0][0], ncol[0][1], ncol[1][0], ncol[1][1],
                y0, y1, 4, ((blockX - 5) & 7) / 8.0, ((blockZ    ) & 7) / 8.0);
        h11 = getSurfaceHeight(ncol[0][0], ncol[0][1], ncol[1][0], ncol[1][1],
                y0, y1, 4, ((blockX - 5) & 7) / 8.0, ((blockZ - 5) & 7) / 8.0);
        break;

    case 3: // (+-) 270
        ssa_snce(ncol[2][0], sn, en, cellx+2, cellz+0, y0, y1);
        ssa_snce(ncol[2][1], sn, en, cellx+2, cellz+1, y0, y1);
        h01 = getSurfaceHeight(ncol[0][0], ncol[0][1], ncol[1][0], ncol[1][1],
                y0, y1, 4, ((blockX    ) & 7) / 8.0, ((blockZ - 5) & 7) / 8.0);
        h10 = getSurfaceHeight(ncol[1][0], ncol[1][1], ncol[2][0], ncol[2][1],
                y0, y1, 4, ((blockX + 5) & 7) / 8.0, ((blockZ    ) & 7) / 8.0);
        h11 = getSurfaceHeight(ncol[1][0], ncol[1][1], ncol[2][0], ncol[2][1],
                y0, y1, 4, ((blockX + 5) & 7) / 8.0, ((blockZ - 5) & 7) / 8.0);
        break;

    default:
        return 0; // error
    }
    //printf("%d %d %d %d\n", h00, h01, h10, h11);
    if (h01 < h00) h00 = h01;
    if (h10 < h00) h00 = h10;
    if (h11 < h00) h00 = h11;
    return h00;
}
#undef ssa_snce
uint64_t getPopulationSeed(int mc, uint64_t ws, int x, int z);

typedef struct { int x0, y0, z0, x1, y1, z1, rot, type; } Pc;
typedef struct {
    int ok, x, z, cx, cz, n, ship;   /* x, z: getStructurePos block; cx, cz: start chunk */
    int bx0, bz0, bx1, bz1;          /* xz bounds of all pieces */
    Pc p[END_CITY_PIECES_MAX];
} City;

static int gen_city(City *c, uint64_t seed, int rx, int rz)
{
    Pos q;
    c->ok = 0;
    if (!getStructurePos(End_City, MC, seed, rx, rz, &q)) return 0;
    static __thread Piece pc[END_CITY_PIECES_MAX];
    int n = getEndCityPieces(pc, seed, q.x >> 4, q.z >> 4);
    c->x = q.x; c->z = q.z; c->cx = q.x >> 4; c->cz = q.z >> 4; c->n = n; c->ship = -1;
    c->bx0 = c->bz0 = 1 << 30; c->bx1 = c->bz1 = -(1 << 30);
    for (int i = 0; i < n; i++) {
        /* cubiomes keeps the pre-1.18 start at block 8 of the chunk; the game uses 7 */
        Pc *p = &c->p[i];
        p->x0 = pc[i].bb0.x - 1; p->z0 = pc[i].bb0.z - 1; p->y0 = pc[i].bb0.y;
        p->x1 = pc[i].bb1.x - 1; p->z1 = pc[i].bb1.z - 1; p->y1 = pc[i].bb1.y;
        p->rot = pc[i].rot & 3; p->type = pc[i].type;
        if (p->type == END_SHIP && c->ship < 0) c->ship = i;
        if (p->x0 < c->bx0) c->bx0 = p->x0;
        if (p->z0 < c->bz0) c->bz0 = p->z0;
        if (p->x1 > c->bx1) c->bx1 = p->x1;
        if (p->z1 > c->bz1) c->bz1 = p->z1;
    }
    c->ok = 1;
    return 1;
}

static void to_world(const Pc *p, int lx, int ly, int lz, int *o)
{
    o[1] = p->y0 + ly;
    switch (p->rot) {
    case 0: o[0] = p->x0 + lx; o[2] = p->z0 + lz; break;
    case 1: o[0] = p->x1 - lz; o[2] = p->z0 + lx; break;
    case 2: o[0] = p->x1 - lx; o[2] = p->z1 - lz; break;
    default: o[0] = p->x0 + lz; o[2] = p->z1 - lx; break;
    }
}

/* class placed by piece p at world (x, y, z) (y relative to the piece's ground),
 * -1 = nothing placed there */
static int piece_cell(const Pc *p, int x, int y, int z)
{
    if (x < p->x0 || x > p->x1 || z < p->z0 || z > p->z1 || y < p->y0 || y > p->y1) return -1;
    int lx, lz, ly = y - p->y0;
    switch (p->rot) {
    case 0: lx = x - p->x0; lz = z - p->z0; break;
    case 1: lz = p->x1 - x; lx = z - p->z0; break;
    case 2: lx = p->x1 - x; lz = p->z1 - z; break;
    default: lz = x - p->x0; lx = p->z1 - z; break;
    }
    const Tpl *t = &TPL[p->type];
    if (!t->c || lx < 0 || lx >= t->sx || ly < 0 || ly >= t->sy || lz < 0 || lz >= t->sz) return -1;
    int c = t->c[(lx * t->sy + ly) * t->sz + lz];
    if (c == 3) return -1;
    if (c == 0 && !t->ow) return -1;
    return c;
}

/* ── fastutil LongOpenHashSet order of the start keys referenced by a chunk ─── */
static uint64_t hmix(uint64_t x) { uint64_t h = x * 0x9E3779B97F4A7C15ULL; h ^= h >> 32; return h ^ (h >> 16); }
static uint64_t ckey(int cx, int cz) { return ((uint64_t)(uint32_t)cx) | ((uint64_t)(uint32_t)cz << 32); }

/* order[] receives the indexes (into cs) in placement order; returns their number */
static int ref_order(City **cs, int nc, int chx, int chz, int *order)
{
    int ref[8], nr = 0;
    for (int i = 0; i < nc; i++) {
        City *c = cs[i];
        if (abs(c->cx - chx) > 8 || abs(c->cz - chz) > 8) continue;
        if (c->bx1 < chx * 16 || c->bx0 > chx * 16 + 15 || c->bz1 < chz * 16 || c->bz0 > chz * 16 + 15)
            continue;
        ref[nr++] = i;
    }
    /* insertion order: createReferences loops source x outer, z inner */
    for (int a = 0; a < nr; a++) for (int b = a + 1; b < nr; b++) {
        City *p = cs[ref[a]], *q = cs[ref[b]];
        if (q->cx < p->cx || (q->cx == p->cx && q->cz < p->cz)) { int t = ref[a]; ref[a] = ref[b]; ref[b] = t; }
    }
    int tab[32]; for (int i = 0; i < 32; i++) tab[i] = -1;
    for (int a = 0; a < nr; a++) {
        int s = (int)(hmix(ckey(cs[ref[a]]->cx, cs[ref[a]]->cz)) & 31);
        while (tab[s] >= 0) s = (s + 1) & 31;
        tab[s] = ref[a];
    }
    int n = 0;
    for (int s = 31; s >= 0; s--) if (tab[s] >= 0) order[n++] = tab[s];
    return n;
}

/* final structure blocks of column (x, z): col[y - YLO] (y relative to A's ground);
 * dy[i]: ground of city i relative to A's. -1 = nothing (air / not placed) */
static void column(City **cs, const int *dy, int nc, int x, int z, int8_t *col)
{
    memset(col, -1, YN);
    int order[8];
    int n = ref_order(cs, nc, x >> 4, z >> 4, order);
    for (int k = 0; k < n; k++) {
        City *c = cs[order[k]];
        for (int i = 0; i < c->n; i++) {
            const Pc *p = &c->p[i];
            if (x < p->x0 || x > p->x1 || z < p->z0 || z > p->z1) continue;
            for (int y = p->y0; y <= p->y1; y++) {
                int v = piece_cell(p, x, y, z);
                if (v < 0) continue;
                int yy = y + dy[order[k]] - YLO;
                if (yy < 0 || yy >= YN) continue;
                col[yy] = (int8_t)(v == 0 ? -1 : v);
            }
        }
    }
}

/* the return gateway's block at (dx, dy, dz) from its centre: 'G', 0 air, 1 bedrock */
static int gw_cell(int dx, int dy, int dz)
{
    int sx = dx == 0, sz = dz == 0, sy = dy == 0;
    if (sx && sz && sy) return 'G';
    if (sy) return 0;
    if ((dy == 2 || dy == -2) && sx && sz) return 1;
    if ((!sx && !sz) || dy == 2 || dy == -2) return 0;
    return 1;
}

/* ── stage A: slots of a (ship city A, neighbour B) pair ─────────────────────── */
typedef struct { int dy, gx, gz, hs, kind, xc, popb; } Slot;   /* hs: struct first-free (rel), -9999 none */
enum { K_SAME = 0, K_ORTHO = 1, K_DIAG = 2 };

static int frame_cells(City *A, int *f, int *s)
{
    const Pc *sh = &A->p[A->ship];
    to_world(sh, 6, 5, 7, f);       /* the « Elytra » data marker = the item frame */
    to_world(sh, 6, 5, 6, s);       /* its support block (frame faces template south) */
    return abs((f[0] >> 4) - A->cx) <= 8 && abs((f[2] >> 4) - A->cz) <= 8;
}

/* B's pieces in the frame's 3x3 neighbourhood, in a chunk B references */
static int b_near(City *B, const int *f)
{
    for (int x = f[0] - 1; x <= f[0] + 1; x++) for (int z = f[2] - 1; z <= f[2] + 1; z++) {
        if (abs((x >> 4) - B->cx) > 8 || abs((z >> 4) - B->cz) > 8) continue;
        for (int i = 0; i < B->n; i++) {
            const Pc *p = &B->p[i];
            if (x >= p->x0 && x <= p->x1 && z >= p->z0 && z <= p->z1) return 1;
        }
    }
    return 0;
}

static __thread char g_popdy[2 * DYMAX + 1];
static int pair_slots(City *A, City *B, Slot *out, int maxo, int *npop)
{
    int f[3], s[3];
    frame_cells(A, f, s);
    City *cs[2] = { A, B };
    int ns = 0;
    *npop = 0;
    static __thread int8_t cols[9][YN];
    for (int dyb = -DYMAX; dyb <= DYMAX; dyb++) {
        int dy[2] = { 0, dyb };
        for (int i = 0; i < 9; i++) column(cs, dy, 2, f[0] - 1 + i / 3, f[2] - 1 + i % 3, cols[i]);
        int si = (s[0] - f[0] + 1) * 3 + (s[2] - f[2] + 1);
        int fyy = f[1] - YLO;
        int supc = cols[si][fyy], frc = cols[4][fyy];
        int popb = supc != 1 || frc == 1 || frc == 2;
        g_popdy[dyb + DYMAX] = (char)popb;
        if (popb) (*npop)++;
        for (int i = 0; i < 9; i++) {
            int gx = f[0] - 1 + i / 3, gz = f[2] - 1 + i % 3;
            int top = -1;
            for (int y = YN - 1; y >= 0; y--) if (cols[i][y] == 1) { top = y; break; }
            int hs = top < 0 ? -9999 : top + YLO + 1;          /* first free y (rel) */
            if (hs > f[1] - 3) continue;                       /* gateway always above */
            /* the frame drops? (support not solid, or a block in its cell) after the box */
            int sup = supc, fr = frc;
            int ddx = s[0] - gx, ddz = s[2] - gz;
            if (abs(ddx) <= 1 && abs(ddz) <= 1) { int v = gw_cell(ddx, 0, ddz); sup = v == 1 ? 1 : -1; }
            ddx = f[0] - gx; ddz = f[2] - gz;
            if (abs(ddx) <= 1 && abs(ddz) <= 1) { int v = gw_cell(ddx, 0, ddz); fr = v == 1 ? 1 : -1; }
            int pops = sup != 1 || fr == 1 || fr == 2;
            if (!pops) continue;
            int kind = (gx == f[0] && gz == f[2]) ? K_SAME : (gx == f[0] || gz == f[2]) ? K_ORTHO : K_DIAG;
            /* cross-chunk: the support / frame cell in another chunk where a structure
             * places a block, decorated after the gateway's chunk -> undone (not deterministic) */
            int xc = 0;
            if ((s[0] >> 4) != (gx >> 4) || (s[2] >> 4) != (gz >> 4)) xc |= 1;
            if ((f[0] >> 4) != (gx >> 4) || (f[2] >> 4) != (gz >> 4)) xc |= 2;
            if (ns < maxo) out[ns++] = (Slot){ dyb, gx, gz, hs, kind, xc, popb };
        }
    }
    return ns;
}

/* ── stage B ─────────────────────────────────────────────────────────────── */
static int gw_at(uint64_t seed, int chx, int chz, int *gx, int *gz, int *k)
{
    Xoroshiro xr;
    StructureConfig sc;
    getStructureConfig(End_Gateway, MC, &sc);
    xSetSeed(&xr, getPopulationSeed(MC, seed, chx * 16, chz * 16) + sc.salt);
    if (xNextFloat(&xr) >= sc.rarity) return 0;
    *gx = chx * 16 + xNextIntJ(&xr, 16);
    *gz = chz * 16 + xNextIntJ(&xr, 16);
    *k = xNextIntJ(&xr, 7) + 3;     /* offset y: uniform 3..9 (Mth.randomBetweenInclusive) */
    return 1;
}

static pthread_mutex_t out_mx = PTHREAD_MUTEX_INITIALIZER;
static atomic_long g_done, g_cfg, g_ships, g_hit, g_viab, g_dym, g_c1, g_c2, g_c3;

static void stage_b(uint64_t L, City *A, City *B, Slot *sl, int ns, long *nhit)
{
    int f[3], s[3];
    frame_cells(A, f, s);
    int chs[16][2], nch = 0;
    for (int i = 0; i < ns; i++) {
        int cx = sl[i].gx >> 4, cz = sl[i].gz >> 4, j;
        for (j = 0; j < nch; j++) if (chs[j][0] == cx && chs[j][1] == cz) break;
        if (j == nch && nch < 16) { chs[nch][0] = cx; chs[nch][1] = cz; nch++; }
    }
    for (uint64_t u = 0; u < 65536; u++) {
        uint64_t seed = (u << 48) | L;
        for (int c = 0; c < nch; c++) {
            int gx, gz, k;
            if (!gw_at(seed, chs[c][0], chs[c][1], &gx, &gz, &k)) continue;
            int any = 0;
            /* a slot of this column whose struct floor gives this k, or whose floor
             * may come from the terrain (lower struct floor: terrain decides) */
            for (int i = 0; i < ns; i++)
                if (sl[i].gx == gx && sl[i].gz == gz && f[1] - sl[i].hs >= k) { any = 1; break; }
            if (!any) continue;
            (*nhit)++;
            /* the expensive part, rare: biomes, grounds, terrain */
            Generator g;
            setupGenerator(&g, MC, 0);
            applySeed(&g, DIM_END, seed);
            if (!isViableStructurePos(End_Gateway, &g, gx, gz, 0)) continue;
            atomic_fetch_add(&g_c1, 1);
            if (!isViableStructurePos(End_City, &g, A->x, A->z, 0)) continue;
            if (!isViableStructurePos(End_City, &g, B->x, B->z, 0)) continue;
            atomic_fetch_add(&g_c2, 1);
            SurfaceNoise sn;
            initSurfaceNoise(&sn, DIM_END, seed);
            int ga = city_ground(&g, &sn, A->x, A->z);
            int gb = city_ground(&g, &sn, B->x, B->z);
            if (ga < 60 || gb < 60) continue;
            atomic_fetch_add(&g_viab, 1);
            int dyb = gb - ga;
            int t = getEndSurfaceHeight(MC, seed, gx, gz);
            int ht = (t > 0 ? t + 1 : 0) - ga;                 /* terrain first free (rel) */
            for (int i = 0; i < ns; i++) {
                Slot *q = &sl[i];
                if (q->dy != dyb || q->gx != gx || q->gz != gz) continue;
                atomic_fetch_add(&g_dym, 1);
                int h = q->hs > ht ? q->hs : ht;
                if (f[1] - h != k) {
                    /* near miss: everything right but the gateway's random height. Its
                     * predicted Y can be checked in game (heightmap over both cities) */
                    pthread_mutex_lock(&out_mx);
                    printf("NEAR seed=%lld kind=%s elytra=%d,%d,%d gateway=%d,%d,%d (needed y %d) "
                           "cityA=%d,%d cityB=%d,%d\n", (long long)seed,
                           q->kind == K_SAME ? "same" : q->kind == K_ORTHO ? "ortho" : "diag",
                           f[0], f[1] + ga, f[2], gx, h + k + ga, gz, f[1] + ga, A->x, A->z, B->x, B->z);
                    fflush(stdout);
                    pthread_mutex_unlock(&out_mx);
                    continue;
                }
                pthread_mutex_lock(&out_mx);
                printf("FOUND seed=%lld kind=%s xc=%d popB=%d elytra=%d,%d,%d gateway=%d,%d,%d "
                       "cityA=%d,%d cityB=%d,%d groundA=%d groundB=%d\n",
                       (long long)seed, q->kind == K_SAME ? "same" : q->kind == K_ORTHO ? "ortho" : "diag",
                       q->xc, q->popb, f[0], f[1] + ga, f[2], gx, f[1] + ga, gz,
                       A->x, A->z, B->x, B->z, ga, gb);
                fflush(stdout);
                pthread_mutex_unlock(&out_mx);
            }
        }
    }
}

/* ── driver ──────────────────────────────────────────────────────────────── */
typedef struct { uint64_t L0, count; int radius, nth, id, mode; uint64_t upper; } Job;

static void *worker(void *arg)
{
    Job *j = (Job *)arg;
    int rr = j->radius / 320 + 2;
    int W = 2 * rr + 1;
    City *cities = (City *)malloc(sizeof(City) * W * W);
    signed char *st = (signed char *)malloc(W * W);
    Slot *sl = (Slot *)malloc(sizeof(Slot) * 20000);
    for (uint64_t L = j->L0 + j->id; L < j->L0 + j->count; L += j->nth) {
        memset(st, 0, W * W);          /* 0 not generated, 1 city, 2 none */
        long ships = 0;
        for (int i = 0; i < W; i++) for (int k = 0; k < W; k++) {
            int rx = i - rr, rz = k - rr;
            /* region of the ship city within the radius (and out of the main island) */
            long long cxm = rx * 320LL + 160, czm = rz * 320LL + 160;
            long long d2 = cxm * cxm + czm * czm;
            if (d2 > (long long)(j->radius + 230) * (j->radius + 230) || d2 < 700LL * 700LL) continue;
            City *A = &cities[i * W + k];
            {   /* B's start within 9 chunks of the frame, the frame within 8 of A's start */
                Pos pa, pb;
                if (!getStructurePos(End_City, MC, L, rx, rz, &pa)) continue;
                int close = 0;
                for (int di = -1; di <= 1 && !close; di++) for (int dk = -1; dk <= 1 && !close; dk++) {
                    if ((!di && !dk) || !getStructurePos(End_City, MC, L, rx + di, rz + dk, &pb)) continue;
                    close = abs((pa.x >> 4) - (pb.x >> 4)) <= 17 && abs((pa.z >> 4) - (pb.z >> 4)) <= 17;
                }
                if (!close) continue;
            }
            if (!st[i * W + k]) st[i * W + k] = gen_city(A, L, rx, rz) ? 1 : 2;
            if (st[i * W + k] != 1 || A->ship < 0) continue;
            int f[3], s[3];
            if (!frame_cells(A, f, s)) continue;
            if ((long long)f[0] * f[0] + (long long)f[2] * f[2] > (long long)j->radius * j->radius) continue;
            ships++;
            for (int di = -1; di <= 1; di++) for (int dk = -1; dk <= 1; dk++) {
                if (!di && !dk) continue;
                int i2 = i + di, k2 = k + dk;
                if (i2 < 0 || i2 >= W || k2 < 0 || k2 >= W) continue;
                City *B = &cities[i2 * W + k2];
                {   Pos pb;
                    if (!getStructurePos(End_City, MC, L, rx + di, rz + dk, &pb)) continue;
                    if (abs((pb.x >> 4) - (f[0] >> 4)) > 9 || abs((pb.z >> 4) - (f[2] >> 4)) > 9) continue;
                }
                if (!st[i2 * W + k2]) st[i2 * W + k2] = gen_city(B, L, rx + di, rz + dk) ? 1 : 2;
                if (st[i2 * W + k2] != 1) continue;
                if (!b_near(B, f)) continue;
                int npop;
                int ns = pair_slots(A, B, sl, 20000, &npop);
                if (j->mode == 2 && npop) {
                    /* a real seed (upper bits) where both cities exist with a ground
                     * difference that carves the frame: an in-game test case */
                    Generator g;
                    setupGenerator(&g, MC, 0);
                    for (uint64_t u = 0; u < 65536; u++) {
                        uint64_t seed = (u << 48) | L;
                        applySeed(&g, DIM_END, seed);
                        if (!isViableStructurePos(End_City, &g, A->x, A->z, 0)) continue;
                        if (!isViableStructurePos(End_City, &g, B->x, B->z, 0)) continue;
                        SurfaceNoise sn;
                        initSurfaceNoise(&sn, DIM_END, seed);
                        int ga = city_ground(&g, &sn, A->x, A->z), gb = city_ground(&g, &sn, B->x, B->z);
                        if (ga < 60 || gb < 60 || abs(gb - ga) > DYMAX || !g_popdy[gb - ga + DYMAX]) continue;
                        pthread_mutex_lock(&out_mx);
                        printf("POP seed=%lld elytra_frame=%d,%d,%d cityA=%d,%d (ground %d) cityB=%d,%d (ground %d)\n",
                               (long long)seed, f[0], f[1] + ga, f[2], A->x, A->z, ga, B->x, B->z, gb);
                        fflush(stdout);
                        pthread_mutex_unlock(&out_mx);
                        break;
                    }
                }
                if (!ns) continue;
                atomic_fetch_add(&g_cfg, 1);
                if (j->mode == 3) {
                    pthread_mutex_lock(&out_mx);
                    for (int q = 0; q < ns; q++) printf("SLOT %d %d %d %d\n", sl[q].dy, sl[q].kind, sl[q].xc, sl[q].hs);
                    printf("CFG\n"); fflush(stdout);
                    pthread_mutex_unlock(&out_mx);
                }
                if (j->mode == 1) { long h = 0; stage_b(L, A, B, sl, ns, &h); atomic_fetch_add(&g_hit, h); }
            }
        }
        atomic_fetch_add(&g_ships, ships);
        atomic_fetch_add(&g_done, 1);
    }
    free(cities); free(st); free(sl);
    return NULL;
}

static int run_cut(uint64_t seed, int radius)
{
    Generator g; setupGenerator(&g, MC, 0); applySeed(&g, DIM_END, seed);
    SurfaceNoise sn; initSurfaceNoise(&sn, DIM_END, seed);
    int rr = radius / 320 + 1;
    City *c = (City *)malloc(sizeof(City));
    for (int rx = -rr; rx <= rr; rx++) for (int rz = -rr; rz <= rr; rz++) {
        if (!gen_city(c, seed, rx, rz) || c->ship < 0) continue;
        int f[3], s[3];
        int fin = frame_cells(c, f, s);
        int sin = abs((s[0] >> 4) - c->cx) <= 8 && abs((s[2] >> 4) - c->cz) <= 8;
        if (!fin || sin) continue;
        if ((long long)f[0] * f[0] + (long long)f[2] * f[2] > (long long)radius * radius) continue;
        if (!isViableStructurePos(End_City, &g, c->x, c->z, 0) || !isViableEndCityTerrain(&g, &sn, c->x, c->z)) continue;
        int ga = city_ground(&g, &sn, c->x, c->z);
        printf("CUT seed=%llu city=%d,%d ground=%d elytra_frame=%d,%d,%d support(missing)=%d,%d,%d\n",
               (unsigned long long)seed, c->x, c->z, ga, f[0], f[1] + ga, f[2], s[0], s[1] + ga, s[2]);
    }
    free(c);
    return 0;
}

int main(int argc, char **argv)
{
    if (argc < 2) { fprintf(stderr, "see the header of elytra_search.c\n"); return 1; }
    if (!strcmp(argv[1], "cut") && argc >= 4)
        return run_cut(strtoull(argv[2], 0, 0), atoi(argv[3]));
    int mode = !strcmp(argv[1], "search") ? 1 : !strcmp(argv[1], "pops") ? 2 : !strcmp(argv[1], "slots") ? 3 : 0;
    if (!mode || argc < 6) { fprintf(stderr, "bad arguments\n"); return 1; }
    uint64_t L0 = strtoull(argv[2], 0, 0) & ((1ULL << 48) - 1), cnt = strtoull(argv[3], 0, 0);
    int radius = atoi(argv[4]), nth = atoi(argv[5]);
    if (nth < 1) nth = 1;
    if (nth > 256) nth = 256;
    pthread_t th[256];
    Job jobs[256];
    time_t t0 = time(NULL);
    for (int i = 0; i < nth; i++) {
        jobs[i] = (Job){ L0, cnt, radius, nth, i, mode, 0 };
        pthread_create(&th[i], NULL, worker, &jobs[i]);
    }
    for (;;) {
        struct timespec ts = { 0, 200000000 };
        nanosleep(&ts, NULL);
        long d = atomic_load(&g_done);
        fprintf(stderr, "\r%ld/%llu lower seeds, %ld ships, %ld configs, %ld gateway hits, %ld viable, %ld dY ok, %lds   ",
                d, (unsigned long long)cnt, atomic_load(&g_ships), atomic_load(&g_cfg),
                atomic_load(&g_hit), atomic_load(&g_viab), atomic_load(&g_dym), (long)(time(NULL) - t0));
        if ((uint64_t)d >= cnt) break;
    }
    for (int i = 0; i < nth; i++) pthread_join(th[i], NULL);
    fprintf(stderr, "\nfunnel: gateway in a slot column %ld -> gateway biome ok %ld -> both city biomes %ld"
            " -> both grounds >= 60 %ld -> ground difference has a slot %ld\n", atomic_load(&g_hit),
            atomic_load(&g_c1), atomic_load(&g_c2), atomic_load(&g_viab), atomic_load(&g_dym));
    fprintf(stderr, "\ndone: %ld ships, %ld configs, %ld gateway hits, %lds\n", atomic_load(&g_ships),
            atomic_load(&g_cfg), atomic_load(&g_hit), (long)(time(NULL) - t0));
    return 0;
}
