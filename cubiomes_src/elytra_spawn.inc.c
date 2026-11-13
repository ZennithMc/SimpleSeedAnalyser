/*
 * elytra_spawn.inc.c — included at the end of session_engine.c (24/10).
 * Condition « Elytra on End spawn » (CTYPE_ELYTRA_SPAWN): an End ship's elytra
 * that the game itself teleports to the End spawn platform (100, 48, 0):
 *   city B's piece (mostly its ship: overwrite, a 13x24x29 box of air) is placed
 *   AFTER city A's ship in a chunk and carves A's bow room; a return gateway
 *   (rarity 1/700, MOTION_BLOCKING first free + 3..9) then generates AT THE FRAME
 *   LEVEL in a carved column: its box sets the frame's support to air, the frame
 *   drops the elytra in (same) / next to (ortho, 1 block away) the portal block,
 *   which teleports it to the exact exit (100, 50, 0).
 * Verified in the 26.3 jar and in game (seed 3122954253559898113, same).
 * Same code as tools/elytra/elytra_search.c (the standalone search).
 *   stage A (48 low bits): cities, ships, the 8-chunk reference rule, the
 *     LongOpenHashSet order of the two starts in a chunk, the gateway "slots"
 *     for every ground difference;  -> the 48-bit pre-filter
 *   stage B (full seed): the gateway of the slot chunks, its k, the biomes,
 *     both city grounds, the terrain.  -> the condition
 * SsaCond: step = radius around 0,0 (blocks), struct_type = 1: ortho results too.
 */
#include "elytra_tpl.h"

uint64_t getPopulationSeed(int mc, uint64_t ws, int x, int z);   /* finders.c */

#define ELY_DYMAX 48             /* ground difference B - A explored */
#define ELY_YLO (-80)            /* column cells relative to A's ground */
#define ELY_YN  320

static __thread int ely_mc = MC_NEWEST;

typedef struct { int x0, y0, z0, x1, y1, z1, rot, type; } ElyPc;
typedef struct {
    int ok, x, z, cx, cz, n, ship;   /* x, z: getStructurePos block; cx, cz: start chunk */
    int bx0, bz0, bx1, bz1;          /* xz bounds of all pieces */
    ElyPc p[END_CITY_PIECES_MAX];
} ElyCity;

static int ely_gen_city(ElyCity *c, uint64_t seed, int rx, int rz)
{
    Pos q;
    c->ok = 0;
    if (!getStructurePos(End_City, ely_mc, seed, rx, rz, &q)) return 0;
    static __thread Piece pc[END_CITY_PIECES_MAX];
    int n = getEndCityPieces(pc, seed, q.x >> 4, q.z >> 4);
    c->x = q.x; c->z = q.z; c->cx = q.x >> 4; c->cz = q.z >> 4; c->n = n; c->ship = -1;
    c->bx0 = c->bz0 = 1 << 30; c->bx1 = c->bz1 = -(1 << 30);
    for (int i = 0; i < n; i++) {
        /* cubiomes keeps the pre-1.18 start at block 8 of the chunk; the game uses 7 */
        ElyPc *p = &c->p[i];
        /* (30/10) 1.18.2 still at block 8 (checked on its End server) */
        int o = ely_mc >= MC_1_19_2 ? 1 : 0;
        p->x0 = pc[i].bb0.x - o; p->z0 = pc[i].bb0.z - o; p->y0 = pc[i].bb0.y;
        p->x1 = pc[i].bb1.x - o; p->z1 = pc[i].bb1.z - o; p->y1 = pc[i].bb1.y;
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

static void ely_to_world(const ElyPc *p, int lx, int ly, int lz, int *o)
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
static int ely_piece_cell(const ElyPc *p, int x, int y, int z)
{
    if (x < p->x0 || x > p->x1 || z < p->z0 || z > p->z1 || y < p->y0 || y > p->y1) return -1;
    int lx, lz, ly = y - p->y0;
    switch (p->rot) {
    case 0: lx = x - p->x0; lz = z - p->z0; break;
    case 1: lz = p->x1 - x; lx = z - p->z0; break;
    case 2: lx = p->x1 - x; lz = p->z1 - z; break;
    default: lz = x - p->x0; lx = p->z1 - z; break;
    }
    const ElyTpl *t = &ELY_TPL[p->type];
    if (!t->c || lx < 0 || lx >= t->sx || ly < 0 || ly >= t->sy || lz < 0 || lz >= t->sz) return -1;
    int c = t->c[(lx * t->sy + ly) * t->sz + lz];
    if (c == 3) return -1;
    if (c == 0 && !t->ow) return -1;
    return c;
}

/* ── fastutil LongOpenHashSet order of the start keys referenced by a chunk ─── */
static uint64_t ely_hmix(uint64_t x) { uint64_t h = x * 0x9E3779B97F4A7C15ULL; h ^= h >> 32; return h ^ (h >> 16); }
static uint64_t ely_ckey(int cx, int cz) { return ((uint64_t)(uint32_t)cx) | ((uint64_t)(uint32_t)cz << 32); }

/* order[] receives the indexes (into cs) in placement order; returns their number */
static int ely_ref_order(ElyCity **cs, int nc, int chx, int chz, int *order)
{
    int ref[8], nr = 0;
    for (int i = 0; i < nc; i++) {
        ElyCity *c = cs[i];
        if (abs(c->cx - chx) > 8 || abs(c->cz - chz) > 8) continue;
        if (c->bx1 < chx * 16 || c->bx0 > chx * 16 + 15 || c->bz1 < chz * 16 || c->bz0 > chz * 16 + 15)
            continue;
        ref[nr++] = i;
    }
    /* insertion order: createReferences loops source x outer, z inner */
    for (int a = 0; a < nr; a++) for (int b = a + 1; b < nr; b++) {
        ElyCity *p = cs[ref[a]], *q = cs[ref[b]];
        if (q->cx < p->cx || (q->cx == p->cx && q->cz < p->cz)) { int t = ref[a]; ref[a] = ref[b]; ref[b] = t; }
    }
    int tab[32]; for (int i = 0; i < 32; i++) tab[i] = -1;
    for (int a = 0; a < nr; a++) {
        int s = (int)(ely_hmix(ely_ckey(cs[ref[a]]->cx, cs[ref[a]]->cz)) & 31);
        while (tab[s] >= 0) s = (s + 1) & 31;
        tab[s] = ref[a];
    }
    int n = 0;
    for (int s = 31; s >= 0; s--) if (tab[s] >= 0) order[n++] = tab[s];
    return n;
}

/* final structure blocks of ely_column (x, z): col[y - ELY_YLO] (y relative to A's ground);
 * dy[i]: ground of city i relative to A's. -1 = nothing (air / not placed) */
static void ely_column(ElyCity **cs, const int *dy, int nc, int x, int z, int8_t *col)
{
    memset(col, -1, ELY_YN);
    int order[8];
    int n = ely_ref_order(cs, nc, x >> 4, z >> 4, order);
    for (int k = 0; k < n; k++) {
        ElyCity *c = cs[order[k]];
        for (int i = 0; i < c->n; i++) {
            const ElyPc *p = &c->p[i];
            if (x < p->x0 || x > p->x1 || z < p->z0 || z > p->z1) continue;
            for (int y = p->y0; y <= p->y1; y++) {
                int v = ely_piece_cell(p, x, y, z);
                if (v < 0) continue;
                int yy = y + dy[order[k]] - ELY_YLO;
                if (yy < 0 || yy >= ELY_YN) continue;
                col[yy] = (int8_t)(v == 0 ? -1 : v);
            }
        }
    }
}

/* the return gateway's block at (dx, dy, dz) from its centre: 'G', 0 air, 1 bedrock */
static int ely_gw_cell(int dx, int dy, int dz)
{
    int sx = dx == 0, sz = dz == 0, sy = dy == 0;
    if (sx && sz && sy) return 'G';
    if (sy) return 0;
    if ((dy == 2 || dy == -2) && sx && sz) return 1;
    if ((!sx && !sz) || dy == 2 || dy == -2) return 0;
    return 1;
}

/* ── stage A: slots of a (ship city A, neighbour B) pair ─────────────────────── */
typedef struct { int dy, gx, gz, hs, kind, xc, popb; } ElySlot;   /* hs: struct first-free (rel), -9999 none */
enum { ELY_SAME = 0, ELY_ORTHO = 1, ELY_DIAG = 2 };

static int ely_frame_cells(ElyCity *A, int *f, int *s)
{
    const ElyPc *sh = &A->p[A->ship];
    ely_to_world(sh, 6, 5, 7, f);       /* the « Elytra » data marker = the item frame */
    ely_to_world(sh, 6, 5, 6, s);       /* its support block (frame faces template south) */
    return abs((f[0] >> 4) - A->cx) <= 8 && abs((f[2] >> 4) - A->cz) <= 8;
}

/* B's pieces in the frame's 3x3 neighbourhood, in a chunk B references */
static int ely_b_near(ElyCity *B, const int *f)
{
    for (int x = f[0] - 1; x <= f[0] + 1; x++) for (int z = f[2] - 1; z <= f[2] + 1; z++) {
        if (abs((x >> 4) - B->cx) > 8 || abs((z >> 4) - B->cz) > 8) continue;
        for (int i = 0; i < B->n; i++) {
            const ElyPc *p = &B->p[i];
            if (x >= p->x0 && x <= p->x1 && z >= p->z0 && z <= p->z1) return 1;
        }
    }
    return 0;
}

static __thread char ely_popdy[2 * ELY_DYMAX + 1];
static int ely_pair_slots(ElyCity *A, ElyCity *B, ElySlot *out, int maxo, int *npop)
{
    int f[3], s[3];
    ely_frame_cells(A, f, s);
    ElyCity *cs[2] = { A, B };
    int ns = 0;
    *npop = 0;
    static __thread int8_t cols[9][ELY_YN];
    for (int dyb = -ELY_DYMAX; dyb <= ELY_DYMAX; dyb++) {
        int dy[2] = { 0, dyb };
        for (int i = 0; i < 9; i++) ely_column(cs, dy, 2, f[0] - 1 + i / 3, f[2] - 1 + i % 3, cols[i]);
        int si = (s[0] - f[0] + 1) * 3 + (s[2] - f[2] + 1);
        int fyy = f[1] - ELY_YLO;
        int supc = cols[si][fyy], frc = cols[4][fyy];
        int popb = supc != 1 || frc == 1 || frc == 2;
        ely_popdy[dyb + ELY_DYMAX] = (char)popb;
        if (popb) (*npop)++;
        for (int i = 0; i < 9; i++) {
            int gx = f[0] - 1 + i / 3, gz = f[2] - 1 + i % 3;
            int top = -1;
            for (int y = ELY_YN - 1; y >= 0; y--) if (cols[i][y] == 1) { top = y; break; }
            int hs = top < 0 ? -9999 : top + ELY_YLO + 1;          /* first free y (rel) */
            if (hs > f[1] - 3) continue;                       /* gateway always above */
            /* the frame drops? (support not solid, or a block in its cell) after the box */
            int sup = supc, fr = frc;
            int ddx = s[0] - gx, ddz = s[2] - gz;
            if (abs(ddx) <= 1 && abs(ddz) <= 1) { int v = ely_gw_cell(ddx, 0, ddz); sup = v == 1 ? 1 : -1; }
            ddx = f[0] - gx; ddz = f[2] - gz;
            if (abs(ddx) <= 1 && abs(ddz) <= 1) { int v = ely_gw_cell(ddx, 0, ddz); fr = v == 1 ? 1 : -1; }
            int pops = sup != 1 || fr == 1 || fr == 2;
            if (!pops) continue;
            int kind = (gx == f[0] && gz == f[2]) ? ELY_SAME : (gx == f[0] || gz == f[2]) ? ELY_ORTHO : ELY_DIAG;
            /* cross-chunk: the support / frame cell in another chunk where a structure
             * places a block, decorated after the gateway's chunk -> undone (not deterministic) */
            int xc = 0;
            if ((s[0] >> 4) != (gx >> 4) || (s[2] >> 4) != (gz >> 4)) xc |= 1;
            if ((f[0] >> 4) != (gx >> 4) || (f[2] >> 4) != (gz >> 4)) xc |= 2;
            if (ns < maxo) out[ns++] = (ElySlot){ dyb, gx, gz, hs, kind, xc, popb };
        }
    }
    return ns;
}

static int ely_gw_at(uint64_t seed, int chx, int chz, int *gx, int *gz, int *k)
{
    Xoroshiro xr;
    StructureConfig sc;
    getStructureConfig(End_Gateway, ely_mc, &sc);
    xSetSeed(&xr, getPopulationSeed(ely_mc, seed, chx * 16, chz * 16) + sc.salt);
    if (xNextFloat(&xr) >= sc.rarity) return 0;
    *gx = chx * 16 + xNextIntJ(&xr, 16);
    *gz = chz * 16 + xNextIntJ(&xr, 16);
    *k = xNextIntJ(&xr, 7) + 3;     /* offset y: uniform 3..9 (Mth.randomBetweenInclusive) */
    return 1;
}

/* ── per-thread cache: the configurations of the current 48 low bits ────────── */
typedef struct { ElyCity A, B; ElySlot *sl; int ns, ga, gb; } ElyCfg;   /* sl[].hs: the required k */
typedef struct {
    uint64_t L; int radius, ortho, mc, valid;
    ElyCfg *cfg; int ncfg, capcfg;
    ElyCity *ca, *cb;              /* scratch */
    ElySlot *sl;                   /* scratch slots */
    Generator g; int g_setup;      /* the End of the 48 low bits */
} ElyCache;
static __thread ElyCache ely_cache;

#define ELY_MAXSLOT 20000

static int ely_keep(const ElySlot *q, int ortho)
{
    if (q->xc) return 0;                          /* depends on the chunk order */
    return q->kind == ELY_SAME || (ortho && q->kind == ELY_ORTHO);
}

/* stage A for the 48 low bits L: fills the cache, returns the number of configs */
static int ely_stage_a(uint64_t L, int radius, int ortho, int mc)
{
    ElyCache *C = &ely_cache;
    L &= 0xFFFFFFFFFFFFULL;
    if (C->valid && C->L == L && C->radius == radius && C->ortho == ortho && C->mc == mc)
        return C->ncfg;
    ely_mc = mc;
    if (!C->ca) {
        C->ca = (ElyCity *)malloc(sizeof(ElyCity));
        C->cb = (ElyCity *)malloc(sizeof(ElyCity));
        C->sl = (ElySlot *)malloc(sizeof(ElySlot) * ELY_MAXSLOT);
        if (!C->ca || !C->cb || !C->sl) return 0;
    }
    for (int i = 0; i < C->ncfg; i++) free(C->cfg[i].sl);
    C->ncfg = 0;
    C->L = L; C->radius = radius; C->ortho = ortho; C->mc = mc; C->valid = 1;
    if (mc < MC_1_18 || radius <= 0) return 0;
    int rr = radius / 320 + 2;
    for (int rx = -rr; rx <= rr; rx++) for (int rz = -rr; rz <= rr; rz++) {
        long long cxm = rx * 320LL + 160, czm = rz * 320LL + 160;
        long long d2 = cxm * cxm + czm * czm;
        if (d2 > (long long)(radius + 230) * (radius + 230) || d2 < 700LL * 700LL) continue;
        Pos pa, pb;
        if (!getStructurePos(End_City, mc, L, rx, rz, &pa)) continue;
        int close = 0;          /* B's start within 9 chunks of the frame, the frame within 8 of A's */
        for (int di = -1; di <= 1 && !close; di++) for (int dk = -1; dk <= 1 && !close; dk++) {
            if ((!di && !dk) || !getStructurePos(End_City, mc, L, rx + di, rz + dk, &pb)) continue;
            close = abs((pa.x >> 4) - (pb.x >> 4)) <= 17 && abs((pa.z >> 4) - (pb.z >> 4)) <= 17;
        }
        if (!close) continue;
        ElyCity *A = C->ca;
        if (!ely_gen_city(A, L, rx, rz) || A->ship < 0) continue;
        int f[3], s[3];
        if (!ely_frame_cells(A, f, s)) continue;
        if ((long long)f[0] * f[0] + (long long)f[2] * f[2] > (long long)radius * radius) continue;
        for (int di = -1; di <= 1; di++) for (int dk = -1; dk <= 1; dk++) {
            if (!di && !dk) continue;
            if (!getStructurePos(End_City, mc, L, rx + di, rz + dk, &pb)) continue;
            if (abs((pb.x >> 4) - (f[0] >> 4)) > 9 || abs((pb.z >> 4) - (f[2] >> 4)) > 9) continue;
            ElyCity *B = C->cb;
            if (!ely_gen_city(B, L, rx + di, rz + dk)) continue;
            if (!ely_b_near(B, f)) continue;
            int npop;
            int ns = ely_pair_slots(A, B, C->sl, ELY_MAXSLOT, &npop), nk = 0;
            for (int q = 0; q < ns; q++) if (ely_keep(&C->sl[q], ortho)) C->sl[nk++] = C->sl[q];
            if (!nk) continue;
            /* The End (biomes, island terrain) only depends on the 48 low bits
             * (Java LCG seeding): the cities' biomes and grounds, the gateway
             * biome and the terrain under it are known here; only the gateway
             * itself (population seed, xoroshiro) needs the full seed. */
            if (!C->g_setup || C->g.mc != mc) { setupGenerator(&C->g, mc, 0); C->g_setup = 1; }
            if (C->g.seed != L || C->g.dim != DIM_END) applySeed(&C->g, DIM_END, L);
            if (!isViableStructurePos(End_City, &C->g, A->x, A->z, 0)) continue;
            if (!isViableStructurePos(End_City, &C->g, B->x, B->z, 0)) continue;
            SurfaceNoise sn;
            initSurfaceNoise(&sn, DIM_END, L);
            int ga = ssa_end_city_ground_full(&C->g, &sn, A->x, A->z);
            int gb = ssa_end_city_ground_full(&C->g, &sn, B->x, B->z);
            if (ga < 60 || gb < 60) continue;
            int nk2 = 0;
            for (int q = 0; q < nk; q++) {
                ElySlot sq = C->sl[q];
                if (sq.dy != gb - ga) continue;
                if (!isViableStructurePos(End_Gateway, &C->g, sq.gx, sq.gz, 0)) continue;
                int t = getEndSurfaceHeight(mc, L, sq.gx, sq.gz);
                int ht = (t > 0 ? t + 1 : 0) - ga;         /* terrain first free (rel) */
                int h = sq.hs > ht ? sq.hs : ht;
                int k = f[1] - h;
                if (k < 3 || k > 9) continue;
                sq.hs = k;
                C->sl[nk2++] = sq;
            }
            nk = nk2;
            if (!nk) continue;
            if (C->ncfg == C->capcfg) {
                int nc = C->capcfg ? 2 * C->capcfg : 4;
                ElyCfg *p = (ElyCfg *)realloc(C->cfg, sizeof(ElyCfg) * nc);
                if (!p) return C->ncfg;
                C->cfg = p; C->capcfg = nc;
            }
            ElyCfg *g = &C->cfg[C->ncfg];
            g->sl = (ElySlot *)malloc(sizeof(ElySlot) * nk);
            if (!g->sl) return C->ncfg;
            memcpy(g->sl, C->sl, sizeof(ElySlot) * nk);
            g->ns = nk; g->ga = ga; g->gb = gb;
            memcpy(&g->A, A, sizeof(ElyCity));
            memcpy(&g->B, B, sizeof(ElyCity));
            C->ncfg++;
        }
    }
    return C->ncfg;
}

/* out[16]: kind (0 same, 1 ortho), elytra x y z, gateway x y z, city A x z,
 * city B x z, ground A, ground B, xc, popB */
static int ely_stage_b(uint64_t seed, int radius, int ortho, int mc, int32_t *out)
{
    int n = ely_stage_a(seed, radius, ortho, mc);
    if (!n) return 0;
    ely_mc = mc;
    int best = 0;
    for (int ci = 0; ci < n; ci++) {
        ElyCfg *cf = &ely_cache.cfg[ci];
        int f[3], s[3];
        ely_frame_cells(&cf->A, f, s);
        int done[16][2], nd = 0;
        for (int i = 0; i < cf->ns; i++) {
            ElySlot *q = &cf->sl[i];
            int cx = q->gx >> 4, cz = q->gz >> 4, j;
            for (j = 0; j < nd; j++) if (done[j][0] == cx && done[j][1] == cz) break;
            if (j < nd) continue;
            if (nd < 16) { done[nd][0] = cx; done[nd][1] = cz; nd++; }
            int gx, gz, k;
            if (!ely_gw_at(seed, cx, cz, &gx, &gz, &k)) continue;
            for (int m = 0; m < cf->ns; m++) {
                ElySlot *r = &cf->sl[m];
                if (r->gx != gx || r->gz != gz || r->hs != k) continue;
                if (best && out[0] <= r->kind) continue;          /* keep the best kind */
                out[0] = r->kind;
                out[1] = f[0]; out[2] = f[1] + cf->ga; out[3] = f[2];
                out[4] = gx;   out[5] = f[1] + cf->ga; out[6] = gz;
                out[7] = cf->A.x; out[8] = cf->A.z; out[9] = cf->B.x; out[10] = cf->B.z;
                out[11] = cf->ga; out[12] = cf->gb; out[13] = r->xc; out[14] = r->popb; out[15] = 0;
                best = 1;
            }
        }
    }
    return best;
}

/* the condition (eval_condition) */
static int ely_eval(SsaEnv *env, const SsaCond *c, int cond_idx)
{
    int32_t o[16];
    if (env->mc < MC_1_18) return c->exclude ? (env_push(env, cond_idx, 0, 0), 1) : 0;
    int ok = ely_stage_b(env->seed, c->step, c->struct_type == 1, env->mc, o);
    if (c->exclude) ok = !ok;
    if (!ok) return 0;
    if (c->exclude) env_push(env, cond_idx, 0, 0);
    else env_push_y(env, cond_idx, o[1], o[2], o[3]);
    return 1;
}

/* the 48-bit pre-filter (ssa_test_seed_pos_only) */
static int ely_pass48(SsaEnv *env, const SsaCond *c)
{
    if (c->exclude) return 1;
    return ely_stage_a(env->seed, c->step, c->struct_type == 1, env->mc) > 0;
}

/* (24/10) the details of a result, for the Location column: see ely_stage_b's out */
EXPORT int ssa_elytra_info(int mc, int64_t seed, int radius, int ortho, int32_t *out16)
{
    if (mc < MC_1_18) return 0;
    return ely_stage_b((uint64_t)seed, radius, ortho, mc, out16);
}
