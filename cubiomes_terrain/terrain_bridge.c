/* terrain_bridge.c — ctypes bridge over xpple/cubiomes terrainnoise (block-perfect
 * 1.18+ terrain surface, caves included). Built into a SEPARATE shared lib
 * (terrain.dll / .so) so its cubiomes symbols never clash with SSA's libcubiomes.
 *
 * Convention: generateColumn(flag=1) returns the Y of the air block directly above
 * the highest solid block. tb_surface_y returns that in world coords (= y-64). The
 * Python layer subtracts 1 if the caller wants the top SOLID block (what you read
 * standing on the surface in-game). Caves/overhangs are already in the density. */
#include "terrainnoise.h"
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <math.h>
#include <float.h>

#ifndef TB_PI
  #define TB_PI 3.14159265358979323846
#endif

#ifdef _WIN32
  #define EXPORT __declspec(dllexport)
  #define TLS    __declspec(thread)
#else
  #define EXPORT
  #define TLS    __thread
#endif

#define NCOL       (48 + 1)
#define CACHE_SIZE 4096          /* power of two; per-seed noise-column cache */

typedef struct { int valid, cx, cz, caves; double col[NCOL]; } ColCache;

typedef struct {
    TerrainNoise tn;
    int      ready;
    int      mc;
    uint64_t seed;
    int      n_used;             /* entrées valides dans le cache */
    ColCache cache[CACHE_SIZE];
} TBContext;

EXPORT void *tb_alloc(void)      { return calloc(1, sizeof(TBContext)); }
EXPORT void  tb_free(void *p)    { free(p); }

/* (re)initialise for a seed+version. Returns 1 on success, 0 on failure. */
EXPORT int tb_init(void *p, int mc, uint64_t seed)
{
    TBContext *c = (TBContext *)p;
    if (c->ready && c->mc == mc && c->seed == seed) return 1;
    memset(c, 0, sizeof(*c));
    if (!setupTerrainNoise(&c->tn, mc, 0)) return 0;
    if (!initTerrainNoise(&c->tn, seed, DIM_OVERWORLD)) return 0;
    c->ready = 1; c->mc = mc; c->seed = seed;
    return 1;
}

/* cached noise column for cell (cx,cz) at the given caves mode (open-addressing,
 * linear probe). caves is part of the key so both modes coexist in the cache. */
static const double *getcol(TBContext *c, int cx, int cz, int caves)
{
    /* BUG corrigé (26/09/2026) : quand le cache était plein (> 4096 cellules, soit
     * une zone d'environ 256×256 blocs déjà vue pour cette graine), getcol renvoyait
     * un tampon STATIQUE partagé ; tb_surface_y gardait 4 pointeurs vers ce même
     * tampon → les 4 colonnes d'angle étaient identiques → Y FAUX sur la carte et au
     * survol après un peu de navigation (et course de données entre threads).
     * Désormais le cache est vidé à 75 % de remplissage (le sondage trouve toujours
     * une case libre), et les appelants copient les colonnes (voir tb_surface_y). */
    if (c->n_used >= (CACHE_SIZE * 3) / 4) {
        int k;
        for (k = 0; k < CACHE_SIZE; k++) c->cache[k].valid = 0;
        c->n_used = 0;
    }
    uint32_t h = (uint32_t)cx * 0x9e3779b1u ^ (uint32_t)cz * 0x85ebca6bu
               ^ (uint32_t)(caves ? 0x6f1u : 0xb27u);
    uint32_t idx = h & (CACHE_SIZE - 1), s;
    for (s = 0; s < CACHE_SIZE; s++) {
        ColCache *e = &c->cache[(idx + s) & (CACHE_SIZE - 1)];
        if (e->valid && e->cx == cx && e->cz == cz && e->caves == caves) return e->col;
        if (!e->valid) {
            sampleNoiseColumnEx(&c->tn, cx, cz, e->col, caves);
            e->valid = 1; e->cx = cx; e->cz = cz; e->caves = caves;
            c->n_used++;
            return e->col;
        }
    }
    return NULL;   /* inatteignable (remplissage ≤ 75 %) */
}

/* world-Y of the air block directly above the highest solid block at (x,z).
 * caves=1 -> full exact surface; caves=0 -> surface without any cave carving. */
EXPORT int tb_surface_y(void *p, int x, int z, int caves)
{
    TBContext *c = (TBContext *)p;
    int cx = x >> 2, cz = z >> 2;
    /* copie immédiate de chaque colonne : un vidage du cache entre deux getcol ne
     * peut plus altérer une colonne déjà obtenue */
    double d00[NCOL], d01[NCOL], d10[NCOL], d11[NCOL];
    memcpy(d00, getcol(c, cx,     cz,     caves), sizeof(d00));
    memcpy(d01, getcol(c, cx,     cz + 1, caves), sizeof(d01));
    memcpy(d10, getcol(c, cx + 1, cz,     caves), sizeof(d10));
    memcpy(d11, getcol(c, cx + 1, cz + 1, caves), sizeof(d11));
    int blocks[384];
    int y = generateColumn(x, z, blocks, d00, d01, d10, d11, 1);
    return y - 64;
}

/* fill out[j*w + i] with the surface Y over a grid (block step `step`) — for maps */
EXPORT void tb_surface_grid(void *p, int x0, int z0, int w, int h, int step, int caves, int *out)
{
    int i, j;
    for (j = 0; j < h; j++)
        for (i = 0; i < w; i++)
            out[j * w + i] = tb_surface_y(p, x0 + i * step, z0 + j * step, caves);
}

/* full block column (384 ids, index 0 = world y -64) for cave/overhang inspection */
EXPORT void tb_column(void *p, int x, int z, int *blocks_out)
{
    TBContext *c = (TBContext *)p;
    int cx = x >> 2, cz = z >> 2;
    double d00[NCOL], d01[NCOL], d10[NCOL], d11[NCOL];
    memcpy(d00, getcol(c, cx,     cz,     1), sizeof(d00));
    memcpy(d01, getcol(c, cx,     cz + 1, 1), sizeof(d01));
    memcpy(d10, getcol(c, cx + 1, cz,     1), sizeof(d10));
    memcpy(d11, getcol(c, cx + 1, cz + 1, 1), sizeof(d11));
    generateColumn(x, z, blocks_out, d00, d01, d10, d11, 0);
}

/* ── Batch Precise-Y search filter (C-native; per-seed noise cache + fast surface).
 * Surface Y convention: -64 = index→world; -1 = top SOLID block (generateColumn /
 * generateColumnFast return the air block ABOVE it). This matches the map hover
 * (terrain.py exact_y does the same -1), so the search Y == the Y shown on the map. */

/* ── Batch context WITH a per-seed noise-column cache ────────────────────────────
 * The disk scan reads the surface at many (x,z); each needs 4 corner noise columns,
 * and adjacent samples SHARE corner cells. Without a cache the batch recomputed the
 * same (expensive) sampleNoiseColumnEx 4× per point → the dominant cost of a radial
 * search. We cache columns per seed (open-addressed hash, generation-tagged so a new
 * seed invalidates everything in O(1) with no memset). This is what lets a radial
 * search approach the per-seed re-seed ceiling (~90k sps) instead of collapsing. */
#define BCACHE_SIZE 16384            /* power of two; covers ~16k distinct cells */
typedef struct { uint32_t gen; int cx, cz, caves; double col[NCOL]; } BCacheEntry;
typedef struct {
    TerrainNoise tn;
    uint32_t     gen;                /* current seed's generation tag */
    BCacheEntry  cache[BCACHE_SIZE];
} TBBatchCtx;

/* start a new seed: bump the generation (all entries become stale instantly). On the
 * rare wrap to 0, clear the gens once so stale gen-0 entries can't false-hit. */
static void tb_batch_newseed(TBBatchCtx *c)
{
    c->gen++;
    if (c->gen == 0) {
        int k;
        for (k = 0; k < BCACHE_SIZE; k++) c->cache[k].gen = 0;
        c->gen = 1;
    }
}

/* cached noise column for cell (cx,cz,caves) under the current generation. */
static const double *getcol_batch(TBBatchCtx *c, int cx, int cz, int caves)
{
    uint32_t h = (uint32_t)cx * 0x9e3779b1u ^ (uint32_t)cz * 0x85ebca6bu
               ^ (uint32_t)(caves ? 0x6f1u : 0xb27u);
    uint32_t idx = h & (BCACHE_SIZE - 1), s;
    for (s = 0; s < BCACHE_SIZE; s++) {
        BCacheEntry *e = &c->cache[(idx + s) & (BCACHE_SIZE - 1)];
        if (e->gen == c->gen) {
            if (e->cx == cx && e->cz == cz && e->caves == caves) return e->col;
            continue;                /* live slot, different cell → keep probing */
        }
        /* stale/empty slot → claim and fill it */
        e->gen = c->gen; e->cx = cx; e->cz = cz; e->caves = caves;
        sampleNoiseColumnEx(&c->tn, cx, cz, e->col, caves);
        return e->col;
    }
    /* cache saturated (disk larger than BCACHE_SIZE cells): fall back, no caching */
    { static __thread double fb[NCOL]; sampleNoiseColumnEx(&c->tn, cx, cz, fb, caves); return fb; }
}

/* highest-solid world Y at (x,z) using the per-seed cache (matches tb_surface_y_raw).
 * Each corner column is copied to a local buffer right after fetch so the result is
 * correct even if the (rare) saturation fallback returns a shared scratch buffer. */
static int tb_surface_y_cached(TBBatchCtx *c, int x, int z, int caves)
{
    int cx = x >> 2, cz = z >> 2;
    double d00[NCOL], d01[NCOL], d10[NCOL], d11[NCOL];
    memcpy(d00, getcol_batch(c, cx,     cz,     caves), sizeof(d00));
    memcpy(d01, getcol_batch(c, cx,     cz + 1, caves), sizeof(d01));
    memcpy(d10, getcol_batch(c, cx + 1, cz,     caves), sizeof(d10));
    memcpy(d11, getcol_batch(c, cx + 1, cz + 1, caves), sizeof(d11));
    /* fast surface (skips the sky) — bit-identical to generateColumn(flag=1) */
    return generateColumnFast(x, z, d00, d01, d10, d11) - 64 - 1;
}

/* Filter `n` seeds (ONE chunk). For each seed: re-seed, then scan the disk of
 * radius r around (at_x,at_z) on a grid of step `sample_step`; set mask[i]=1 if
 * ANY sample's Y meets the test (in [y_min,y_max], or OUTSIDE it when invert),
 * else 0. Early-exit per seed. Returns #seeds passed, or -1 on setup failure.
 * setupTerrainNoise (mc only) is done ONCE; only the re-seed is per-seed. The
 * Python layer runs several chunks in parallel threads (ctypes drops the GIL),
 * and each call uses its OWN stack TerrainNoise -> thread-safe. */
/* ── coverage / confidence (Wilson) — same maths as cubiomes-viewer's
 * monteCarloBiomes, but applied to a DETERMINISTIC strided grid so CPU and GPU
 * give identical verdicts (no random sampling, no early-abort). ─────────────── */
static double tb_inverf(double x)
{   /* inverse error function via Newton's method (cubiomes finders.c) */
    double t = x, dt = 1;
    int it = 0;
    while (fabs(dt) > FLT_EPSILON && it++ < 100) {
        dt = 0.5 * sqrt(TB_PI) * (erf(t) - x) / exp(-t * t);
        t -= dt;
    }
    return t;
}
static void tb_wilson(double nn, double p, double z, double *lo, double *hi)
{   /* Wilson score interval (cubiomes finders.c) */
    double s = z * z / nn;
    double t = 1.0 / (1.0 + s);
    double w = t * (p + 0.5 * s);
    double d = t * z * sqrt((p * (1.0 - p) + 0.25 * s) / nn) + FLT_EPSILON;
    *lo = w - d;
    *hi = w + d;
}
/* Decide a coverage test over a strided disk: Ncount sampled cells, Xcount in the
 * wanted Y band. Pass if every sampled cell matches, or the Wilson lower bound at
 * the requested confidence is >= coverage. coverage in (0,1], confidence in [0,1). */
static int tb_coverage_pass(long long Ncount, long long Xcount,
                            double coverage, double confidence)
{
    if (Ncount <= 0) return 0;
    if (Xcount >= Ncount) return 1;          /* all sampled cells match */
    if (Xcount <= 0)      return 0;          /* none match */
    if (confidence >= 1.0) confidence = 0.999999;
    if (confidence < 0.0)  confidence = 0.0;
    double p  = (double)Xcount / (double)Ncount;
    double z  = sqrt(2.0) * tb_inverf(confidence);   /* confidence=0 -> z=0 -> exact p */
    double lo, hi;
    tb_wilson((double)Ncount, p, z, &lo, &hi);
    return (lo >= coverage) ? 1 : 0;
}
/* Smallest number of in-band cells X (1..N) for which tb_coverage_pass(N,X,..) is
 * true. tb_coverage_pass is monotic non-decreasing in X, so binary-search it. This
 * lets the per-seed scan abort the instant the verdict is settled (huge speed-up:
 * most seeds decide after a handful of points instead of scanning the whole disk). */
static long long tb_coverage_threshold(long long N, double coverage, double confidence)
{
    if (N <= 0) return 1;
    long long lo = 1, hi = N, ans = N;
    while (lo <= hi) {
        long long mid = lo + (hi - lo) / 2;
        if (tb_coverage_pass(N, mid, coverage, confidence)) { ans = mid; hi = mid - 1; }
        else lo = mid + 1;
    }
    return ans;
}

/* Allocate + set up a terrain context (mc only; seed-independent). Each worker
 * thread owns ONE — explicit per-thread ownership instead of compiler TLS, which is
 * unreliable for function-local statics in a ctypes-loaded DLL (it resolved to a
 * SINGLE shared struct → all threads re-seeded it at once → random/wrong results). */
EXPORT void *tb_precise_y_alloc(int mc)
{
    TBBatchCtx *c = (TBBatchCtx *)calloc(1, sizeof(TBBatchCtx));   /* gens start at 0 */
    if (!c) return NULL;
    if (!setupTerrainNoise(&c->tn, mc, 0)) { free(c); return NULL; }
    return c;
}
EXPORT void tb_precise_y_free(void *p) { if (p) free(p); }

/* Filter n seeds with a CALLER-OWNED context `p`. Re-seeds the ctx per seed; scans
 * the disk of radius r around (at_x,at_z); mask[i]=1 if any sample's Y meets the test
 * (in [y_min,y_max], or OUTSIDE when invert); early-exit per seed. #passed or -1. */
EXPORT int tb_precise_y_filter_ctx(
    void *p, const uint64_t *seeds, int n,
    int at_x, int at_z, int r, int y_min, int y_max,
    int caves, int invert, int sample_step,
    double coverage, double confidence, uint8_t *mask)
{
    TBBatchCtx *ctx = (TBBatchCtx *)p;
    if (!ctx || n <= 0) return -1;
    if (sample_step < 1) sample_step = 1;
    long long r2 = (long long)r * (long long)r;
    int passed = 0, i;

    /* coverage-mode constants (same for every seed): Ntotal = #cells in the strided
     * disk; Xpass = #in-band cells needed to pass. Computed once → per-seed scans
     * abort the instant Xc>=Xpass (pass) or Xc+remaining<Xpass (fail). */
    int cov_mode = (r > 0 && coverage > 0.0);
    long long Ntotal = 0, Xpass = 1;
    if (cov_mode) {
        int sx, sz;
        for (sz = at_z - r; sz <= at_z + r; sz += sample_step)
            for (sx = at_x - r; sx <= at_x + r; sx += sample_step) {
                long long dx = sx - at_x, dz = sz - at_z;
                if (dx*dx + dz*dz > r2) continue;
                Ntotal++;
            }
        Xpass = tb_coverage_threshold(Ntotal, coverage, confidence);
    }

    for (i = 0; i < n; i++) {
        if (!initTerrainNoise(&ctx->tn, seeds[i], DIM_OVERWORLD)) { mask[i] = 0; continue; }
        tb_batch_newseed(ctx);          /* invalidate the noise-column cache (O(1)) */
        int found = 0;
        if (r <= 0) {
            /* single point — coverage is meaningless */
            int y = tb_surface_y_cached(ctx, at_x, at_z, caves);
            int in = (y >= y_min && y <= y_max);
            found = invert ? !in : in;
        } else if (!cov_mode) {
            /* any-match: pass as soon as ONE sampled cell is in the band (early exit) */
            int sx, sz;
            for (sz = at_z - r; sz <= at_z + r && !found; sz += sample_step) {
                for (sx = at_x - r; sx <= at_x + r; sx += sample_step) {
                    long long dx = sx - at_x, dz = sz - at_z;
                    if (dx*dx + dz*dz > r2) continue;
                    int y = tb_surface_y_cached(ctx, sx, sz, caves);
                    int in = (y >= y_min && y <= y_max);
                    if (invert ? !in : in) { found = 1; break; }
                }
            }
        } else {
            /* coverage mode with deterministic early-abort. Same verdict as a full
             * scan + tb_coverage_pass (and the GPU/numpy reduction), just faster. */
            long long Xc = 0, processed = 0;
            int sx, sz, decided = 0;
            for (sz = at_z - r; sz <= at_z + r && !decided; sz += sample_step) {
                for (sx = at_x - r; sx <= at_x + r; sx += sample_step) {
                    long long dx = sx - at_x, dz = sz - at_z;
                    if (dx*dx + dz*dz > r2) continue;
                    processed++;
                    int y = tb_surface_y_cached(ctx, sx, sz, caves);
                    int in = (y >= y_min && y <= y_max);
                    if (invert ? !in : in) Xc++;
                    if (Xc >= Xpass) { found = 1; decided = 1; break; }
                    if (Xc + (Ntotal - processed) < Xpass) { found = 0; decided = 1; break; }
                }
            }
            if (!decided) found = (Xc >= Xpass);   /* safety (should not happen) */
        }
        mask[i] = (uint8_t)found;
        passed += found;
    }
    return passed;
}

/* Single-call convenience (allocates a temp ctx). For the warm-up probe / one-offs;
 * NOT for the hot multithreaded loop (use alloc + filter_ctx per thread there). */
EXPORT int tb_precise_y_filter(
    const uint64_t *seeds, int n, int mc,
    int at_x, int at_z, int r, int y_min, int y_max,
    int caves, int invert, int sample_step,
    double coverage, double confidence, uint8_t *mask)
{
    void *ctx = tb_precise_y_alloc(mc);
    if (!ctx) return -1;
    int res = tb_precise_y_filter_ctx(ctx, seeds, n, at_x, at_z, r, y_min, y_max,
                                      caves, invert, sample_step,
                                      coverage, confidence, mask);
    tb_precise_y_free(ctx);
    return res;
}
