/*
 * draw_engine.c — "Draw" search engine v2 (27/09).
 *
 * Finds seeds whose map looks like a drawing. Replaces the scoring of
 * drawing_bridge.c (kept for the classic UI) with ONE exact definition used
 * everywhere (search, "test a seed", overlays):
 *
 *   The drawing is a W x H grid of cells, cell = `cell` blocks, centred on
 *   (cx, cz). At offset (dx, dz) (in cells, |d| <= r) cell (i, j) is sampled
 *   at the centre of its world cell:
 *       x = x0 + (i + dx) * cell + cell / 2,   x0 = cx - W * cell / 2
 *   and matches when the world there belongs to the painted class:
 *       1 LAND / 2 OCEAN / 3 MUSHROOM : continentalness (x10000) >= -1900 /
 *                                       in [-10500, -1900) / < -10500
 *       4 HOT / 5 COLD                : temperature (x10000) >= 5500 / <= -4500
 *       1000 + b (b < 500)             : biome b (getBiomeAt scale 4, y = 320,
 *                                       like the map)
 *       1000 + 500 + k                 : any biome of group k (groups[b] bit k)
 *   score(offset) = sum(weight of matching cells) / sum(weight of painted cells)
 *   The reported score is the EXACT score at the reported offset.
 *
 * Mixed drawings (land + hot + jungle...) are fully supported: each cell is
 * judged on its own layer (before: one layer per search, the other cells were
 * silently dropped).
 *
 * Pipeline per seed (no global state; one engine = one search):
 *   1. coarse pre-filter: a coarse drawing (1 cell per s x s block, sampled at
 *      the block's centre cell — no half-cell shift) slid over a coarse map
 *      with few octaves; biome cells are counted as matching (optimistic)
 *      unless the drawing has ONLY biome cells (then biomes are sampled).
 *   2. seeds whose best coarse score reaches the threshold (minus a safety
 *      margin) are refined: around the 3 best coarse positions every fine
 *      offset (+-s) is scored exactly with all octaves and real biomes.
 * Results: every seed >= min_score, best first, capacity top_k.
 */
#if defined(_WIN32)
#  define EXPORT __declspec(dllexport)
#else
#  define EXPORT __attribute__((visibility("default")))
#endif

#include "biomenoise.h"
#include "generator.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifdef _OPENMP
#  include <omp.h>
#endif

#define DC_ANY   0
#define DC_LAND  1
#define DC_OCEAN 2
#define DC_MUSH  3
#define DC_HOT   4
#define DC_COLD  5
#define DC_BIO   1000

#define L_CONT 1
#define L_TEMP 2
#define L_BIO  4

#define BIOME_Y4 (320 >> 2)

typedef struct {
    int      W, H, cell, cx, cz, r, mc, large;
    int32_t *tmpl;          /* W*H class codes                       */
    float   *w;             /* W*H weights (0 where unpainted)       */
    double   wsum;          /* total painted weight                  */
    int      np;            /* painted cells                          */
    int32_t *ord;           /* painted cells, scattered order (i | j<<16) */
    int32_t *ocode;         /* their codes, in that order             */
    float   *ow;            /* their weights                          */
    double  *orem;          /* weight still to test from k on (np+1)  */
    int      layers;        /* L_CONT | L_TEMP | L_BIO present       */
    uint32_t groups[256];   /* biome id -> bit k set when in group k */
    int      modern;        /* 1.18+: climate noises exist           */
    /* coarse drawing */
    int      s, CW, CH, R;
    int32_t *ctmpl;         /* CW*CH                                  */
    int      ncp;           /* coarse cells scored by the coarse passes */
    int32_t *cpos, *ccode;  /* their index in a (CW+2R)-wide grid, code */
    int8_t  *clay;          /* their layer slot (0 cont, 1 temp, 2 bio) */
    float   *cwt;           /* their weight                           */
    float   *cw;            /* CW*CH                                  */
    double   c_fast_w, c_bio_w;   /* coarse weights: fast / biome cells */
    int      coarse_bio;    /* biome-only drawing: sample biomes coarsely */
    int      pre_oct;       /* continentalness octaves of the fast pass */
    double   d_cont, d_temp;/* fast-pass error bound (x 1): optimistic classes */
    double   min_score, margin;   /* margin: coarse drawing vs exact score */
    /* results */
    int      cap, n;
    int64_t *rs; double *rsc; int *rox; int *roz;
    volatile int64_t tested, refined;
#ifdef _OPENMP
    omp_lock_t lock;
#endif
} DEng;

static inline int cls_cont(double c)
{
    double v = c * 10000.0;
    if (v < -10500.0) return DC_MUSH;
    if (v < -1900.0)  return DC_OCEAN;
    return DC_LAND;
}
static inline int cls_temp(double t)
{
    double v = t * 10000.0;
    if (v >= 5500.0)  return DC_HOT;
    if (v <= -4500.0) return DC_COLD;
    return 0;                         /* neither hot nor cold */
}
static inline int layer_of(int32_t code)
{
    if (code == DC_LAND || code == DC_OCEAN || code == DC_MUSH) return L_CONT;
    if (code == DC_HOT || code == DC_COLD) return L_TEMP;
    if (code >= DC_BIO) return L_BIO;
    return 0;
}

/* Per-thread sampler for one seed. */
typedef struct {
    BiomeNoise cont_lo, temp_lo, cont, temp;
    Generator  g;
    int        have_lo, have_full, have_g;
    uint64_t   seed;
} Samp;

static void samp_seed(DEng *e, Samp *sp, uint64_t seed)
{
    sp->seed = seed; sp->have_lo = sp->have_full = sp->have_g = 0;
    (void)e;
}
static void samp_need_lo(DEng *e, Samp *sp)
{
    if (sp->have_lo) return;
    if (e->layers & L_CONT) setClimateParaSeed(&sp->cont_lo, sp->seed, e->large, NP_CONTINENTALNESS, e->pre_oct);
    if (e->layers & L_TEMP) setClimateParaSeed(&sp->temp_lo, sp->seed, e->large, NP_TEMPERATURE, 2);
    sp->have_lo = 1;
}
static void samp_need_full(DEng *e, Samp *sp)
{
    if (sp->have_full) return;
    if (e->layers & L_CONT) setClimateParaSeed(&sp->cont, sp->seed, e->large, NP_CONTINENTALNESS, -1);
    if (e->layers & L_TEMP) setClimateParaSeed(&sp->temp, sp->seed, e->large, NP_TEMPERATURE, -1);
    sp->have_full = 1;
}
static void samp_need_g(DEng *e, Samp *sp)
{
    if (sp->have_g) return;
    applySeed(&sp->g, DIM_OVERWORLD, sp->seed);
    sp->have_g = 1;
}

/* Fast pass: the classes a cell CAN have given the few-octave value v and
 * the bound d of what the missing octaves add (measured: max |full - fast|
 * over 100 000 points, 20 seeds, + margin). Bit k = class code k. */
static inline int mask_cont(double v, double d)
{
    int m = 0;
    if (v + d >= -0.19) m |= 1 << DC_LAND;
    if (v - d < -0.19 && v + d >= -1.05) m |= 1 << DC_OCEAN;
    if (v - d < -1.05) m |= 1 << DC_MUSH;
    return m;
}
static inline int mask_temp(double v, double d)
{
    int m = 1 << DC_ANY;
    if (v + d >= 0.55) m |= 1 << DC_HOT;
    if (v - d <= -0.45) m |= 1 << DC_COLD;
    return m;
}

/* Observed value of ONE layer at a world position.
 * lo = 1: few octaves (pre-filter). Pre-1.18: every layer through biomes. */
static int observe(DEng *e, Samp *sp, int layer, int x, int z, int lo)
{
    if (layer == L_BIO || !e->modern) {
        samp_need_g(e, sp);
        int b = getBiomeAt(&sp->g, 4, x >> 2, BIOME_Y4, z >> 2);
        if (layer == L_BIO) return b;
        /* old versions: land / ocean / mushroom from the biome itself */
        if (b == mushroom_fields || b == mushroom_field_shore) return DC_MUSH;
        return isOceanic(b) ? DC_OCEAN : DC_LAND;
    }
    double xn = x * 0.25, zn = z * 0.25;
    if (layer == L_CONT)
        return cls_cont(sampleClimatePara(lo ? &sp->cont_lo : &sp->cont, NULL, xn, zn));
    return cls_temp(sampleClimatePara(lo ? &sp->temp_lo : &sp->temp, NULL, xn, zn));
}
static inline int match_e(const DEng *e, int32_t code, int obs)
{
    if (code >= DC_BIO + 500) {                 /* biome group k */
        int k = code - DC_BIO - 500;
        return obs >= 0 && obs < 256 && k < 32 && ((e->groups[obs] >> k) & 1u);
    }
    if (code >= DC_BIO) return obs == code - DC_BIO;
    return obs == code;
}
#define match(c, o) match_e(e, (c), (o))

/* ── results ────────────────────────────────────────────────────────────── */
/* Result order: score (best first), then seed (smallest first). A total
 * order: the kept list does not depend on the order the threads report in
 * (ties at the cut used to depend on it). */
static inline int better(double sa, int64_t a, double sb, int64_t b)
{
    return sa > sb || (sa == sb && a < b);
}

static void add_result(DEng *e, int64_t seed, double sc, int ox, int oz)
{
    if (sc < e->min_score) return;
#ifdef _OPENMP
    omp_set_lock(&e->lock);
#endif
    int dup = -1;
    for (int i = 0; i < e->n; i++) if (e->rs[i] == seed) { dup = i; break; }
    if (dup >= 0 && sc > e->rsc[dup]) {   /* re-tested with a better score: move it */
        for (int i = dup; i + 1 < e->n; i++) {
            e->rs[i] = e->rs[i + 1]; e->rsc[i] = e->rsc[i + 1];
            e->rox[i] = e->rox[i + 1]; e->roz[i] = e->roz[i + 1];
        }
        e->n--;
        dup = -1;
    }
    if (dup < 0 && (e->n < e->cap || better(sc, seed, e->rsc[e->n - 1], e->rs[e->n - 1]))) {
        int pos = e->n < e->cap ? e->n++ : e->n - 1;
        while (pos > 0 && better(sc, seed, e->rsc[pos - 1], e->rs[pos - 1])) {
            e->rs[pos] = e->rs[pos - 1]; e->rsc[pos] = e->rsc[pos - 1];
            e->rox[pos] = e->rox[pos - 1]; e->roz[pos] = e->roz[pos - 1]; pos--;
        }
        e->rs[pos] = seed; e->rsc[pos] = sc; e->rox[pos] = ox; e->roz[pos] = oz;
    }
#ifdef _OPENMP
    omp_unset_lock(&e->lock);
#endif
}

static double threshold(DEng *e)
{   /* results worse than the last kept one can not enter a full list */
    double t = e->min_score;
    if (e->n >= e->cap && e->rsc[e->n - 1] > t) t = e->rsc[e->n - 1];
    return t;
}

/* ── per-thread work area: a LAZY full-resolution cache of every layer ───
 * The world is only sampled where a score needs it, once per seed (before:
 * overlapping refinement windows re-sampled the same cells). The coarse
 * full-octave pass reads the same cache (its points are fine-cell centres). */
typedef struct {
    Samp      sp;
    int       M, CWd, CHd;        /* margin (cells) and cache dimensions      */
    int32_t  *val[3];             /* cached observed value per layer          */
    uint32_t *stamp[3];           /* seed stamp of each cached value          */
    uint32_t  cur;                /* stamp of the current seed                */
    int32_t  *g[3];               /* coarse maps (cont, temp, biome)          */
} Work;

static inline int lidx(int layer) { return layer == L_CONT ? 0 : layer == L_TEMP ? 1 : 2; }

static Work *work_new(DEng *e)
{
    Work *w = (Work *)calloc(1, sizeof(Work));
    if (!w) return NULL;
    setupGenerator(&w->sp.g, e->mc, e->large ? LARGE_BIOMES : 0);
    w->M = e->R * e->s + e->s + 1;
    w->CWd = e->W + 2 * w->M; w->CHd = e->H + 2 * w->M;
    size_t n = (size_t)w->CWd * w->CHd;
    int ok = 1;
    for (int l = 0; l < 3; l++) {
        int need = (l == 0 && (e->layers & L_CONT)) || (l == 1 && (e->layers & L_TEMP))
                   || (l == 2 && ((e->layers & L_BIO) || (!e->modern && (e->layers & L_CONT))));
        if (l == 0 && !e->modern) need = 0;       /* pre-1.18: land/ocean from the biome cache */
        if (!need) continue;
        w->val[l] = (int32_t *)malloc(n * sizeof(int32_t));
        w->stamp[l] = (uint32_t *)calloc(n, sizeof(uint32_t));
        ok &= w->val[l] && w->stamp[l];
    }
    size_t gc = (size_t)(e->CW + 2 * e->R) * (e->CH + 2 * e->R);
    for (int l = 0; l < 3; l++) { w->g[l] = (int32_t *)calloc(gc, sizeof(int32_t)); ok &= w->g[l] != NULL; }
    if (!ok) {
        for (int l = 0; l < 3; l++) { free(w->val[l]); free(w->stamp[l]); }
        for (int l = 0; l < 3; l++) free(w->g[l]);
        free(w);
        return NULL;
    }
    return w;
}
static void work_free(Work *w)
{
    if (!w) return;
    for (int l = 0; l < 3; l++) { free(w->val[l]); free(w->stamp[l]); }
    for (int l = 0; l < 3; l++) free(w->g[l]);
    free(w);
}
static void work_seed(DEng *e, Work *w, uint64_t seed)
{
    samp_seed(e, &w->sp, seed);
    if (++w->cur == 0) {                        /* stamp wrap: clear */
        size_t n = (size_t)w->CWd * w->CHd;
        for (int l = 0; l < 3; l++) if (w->stamp[l]) memset(w->stamp[l], 0, n * sizeof(uint32_t));
        w->cur = 1;
    }
}
/* full-octave observed value of `layer` at fine cell (fi, fj) (relative to
 * the drawing's cell (0,0) at offset 0); sampled once per seed */
static int __attribute__((noinline)) full_miss(DEng *e, Work *w, int layer, int fi, int fj)
{
    int l = lidx(layer);
    int ci = fi + w->M, cj = fj + w->M;
    int x = e->cx - (e->W * e->cell) / 2 + fi * e->cell + e->cell / 2;
    int z = e->cz - (e->H * e->cell) / 2 + fj * e->cell + e->cell / 2;
    if (layer != L_BIO && e->modern) samp_need_full(e, &w->sp);
    int v = observe(e, &w->sp, layer, x, z, 0);
    if (ci >= 0 && cj >= 0 && ci < w->CWd && cj < w->CHd) {
        size_t k = (size_t)cj * w->CWd + ci;
        w->val[l][k] = v; w->stamp[l][k] = w->cur;
    }
    return v;
}
static inline int full_raw(DEng *e, Work *w, int layer, int fi, int fj)
{
    int l = lidx(layer);
    int ci = fi + w->M, cj = fj + w->M;
    if ((unsigned)ci < (unsigned)w->CWd && (unsigned)cj < (unsigned)w->CHd) {
        size_t k = (size_t)cj * w->CWd + ci;
        if (w->stamp[l][k] == w->cur) return w->val[l][k];
    }
    return full_miss(e, w, layer, fi, fj);
}
static inline int full_at(DEng *e, Work *w, int layer, int fi, int fj)
{
    if (layer == L_CONT && !e->modern) {        /* pre-1.18: from the biome */
        int b = full_raw(e, w, L_BIO, fi, fj);
        if (b == mushroom_fields || b == mushroom_field_shore) return DC_MUSH;
        return isOceanic(b) ? DC_OCEAN : DC_LAND;
    }
    return full_raw(e, w, layer, fi, fj);
}

/* Exact score at one offset. Branch and bound: cells are tested in a
 * scattered order and the offset is dropped (-1) as soon as even matching
 * every remaining cell could not reach `bound` — so a bad place costs a few
 * samples instead of the whole drawing, and the kept scores stay exact. */
static double exact_at(DEng *e, Work *w, int dx, int dz, double bound)
{
    double m = 0.0, need = bound * e->wsum - 1e-9;
    for (int k = 0; k < e->np; k++) {
        if (m + e->orem[k] < need) return -1.0;
        int32_t c = e->ocode[k], p = e->ord[k];
        if (match(c, full_at(e, w, layer_of(c), (p & 0xffff) + dx, (p >> 16) + dz))) m += e->ow[k];
    }
    if (m < need) return -1.0;
    return e->wsum > 0 ? m / e->wsum : 0.0;
}

/* best exact score over [ox0,ox1] x [oz0,oz1] */
static double exact_window(DEng *e, Work *w, int ox0, int ox1, int oz0, int oz1,
                           double floor_, int *box, int *boz)
{
    double best = floor_; int bx = ox0, bz = oz0, found = 0;
    for (int dz = oz0; dz <= oz1; dz++)
        for (int dx = ox0; dx <= ox1; dx++) {
            double sc = exact_at(e, w, dx, dz, best);
            if (sc >= 0 && (!found || sc > best)) { best = sc; bx = dx; bz = dz; found = 1; }
        }
    if (!found) return -1.0;
    *box = bx; *boz = bz;
    return best;
}
typedef struct { double sc; int dx, dz; } Cand;

static void keep_cand(Cand *out, int *n, int nmax, double sc, int dx, int dz)
{   /* keep the nmax best coarse offsets, at least 2 coarse cells apart */
    int near = -1;
    for (int q = 0; q < *n; q++)
        if (abs(out[q].dx - dx) < 2 && abs(out[q].dz - dz) < 2) { near = q; break; }
    if (near >= 0) {
        if (sc > out[near].sc) { out[near].sc = sc; out[near].dx = dx; out[near].dz = dz; }
    } else if (*n < nmax) {
        out[*n].sc = sc; out[*n].dx = dx; out[*n].dz = dz; (*n)++;
    } else {
        int wq = 0;
        for (int q = 1; q < *n; q++) if (out[q].sc < out[wq].sc) wq = q;
        if (sc > out[wq].sc) { out[wq].sc = sc; out[wq].dx = dx; out[wq].dz = dz; }
    }
}
static void sort_cand(Cand *out, int n)
{
    for (int a = 1; a < n; a++) {
        Cand c = out[a]; int b = a - 1;
        while (b >= 0 && out[b].sc < c.sc) { out[b + 1] = out[b]; b--; }
        out[b + 1] = c;
    }
}

/* Coarse pass. lo = 1: few-octave maps (cheap rejection); lo = 0: the full
 * cache at the coarse points (where to refine). Coarse offset (dx, dz) is in
 * coarse cells; the coarse cell (ci, cj) sits at fine cell (ci*s + s/2, …)
 * and so does its sample: no shift between drawing and map. */
static int coarse_candidates(DEng *e, Work *w, Cand *out, int nmax, int lo)
{
    int R = e->R, s = e->s;
    int GW = e->CW + 2 * R, GH = e->CH + 2 * R;
    int sample_bio = e->coarse_bio;
    int use_lo = lo && e->modern;               /* optimistic class masks */
    if (use_lo && (e->layers & (L_CONT | L_TEMP))) samp_need_lo(e, &w->sp);
    int x0 = e->cx - (e->W * e->cell) / 2, z0 = e->cz - (e->H * e->cell) / 2;
    for (int j = 0; j < GH; j++) {
        int fz = (j - R) * s + s / 2;
        double zn = (z0 + fz * e->cell + e->cell / 2) * 0.25;
        int32_t *gc = w->g[0] + (size_t)j * GW, *gt = w->g[1] + (size_t)j * GW,
                *gb = w->g[2] + (size_t)j * GW;
        for (int i = 0; i < GW; i++) {
            int fx = (i - R) * s + s / 2;
            double xn = (x0 + fx * e->cell + e->cell / 2) * 0.25;
            if (e->layers & L_CONT)
                gc[i] = use_lo ? mask_cont(sampleClimatePara(&w->sp.cont_lo, NULL, xn, zn), e->d_cont)
                               : full_at(e, w, L_CONT, fx, fz);
            if (e->layers & L_TEMP)
                gt[i] = use_lo ? mask_temp(sampleClimatePara(&w->sp.temp_lo, NULL, xn, zn), e->d_temp)
                               : full_at(e, w, L_TEMP, fx, fz);
            if (sample_bio) gb[i] = full_at(e, w, L_BIO, fx, fz);
        }
    }
    int n = 0;
    double tot = e->c_fast_w + e->c_bio_w;
    double base = sample_bio ? 0.0 : e->c_bio_w;               /* optimistic for biomes */
    const int32_t *cpos = e->cpos, *ccode = e->ccode;
    const int8_t *clay = e->clay;
    const float *cwt = e->cwt;
    for (int dz = 0; dz <= 2 * R; dz++)
        for (int dx = 0; dx <= 2 * R; dx++) {
            int off = dz * GW + dx;
            double m = base;
            for (int k = 0; k < e->ncp; k++) {
                int v = w->g[clay[k]][cpos[k] + off];
                int32_t c = ccode[k];
                int ok = clay[k] < 2 ? (use_lo ? (v >> c) & 1 : v == c) : match(c, v);
                if (ok) m += cwt[k];
            }
            keep_cand(out, &n, nmax, tot > 0 ? m / tot : 0.0, dx - R, dz - R);
        }
    sort_cand(out, n);
    return n;
}

/* Full pipeline for one seed. thr < 0: no pre-filter (always refine).
 * Returns the exact best score, -2 when the fast pass rejected the seed,
 * -1 when a later stage did. */
static double eval_seed(DEng *e, Work *wk, uint64_t seed, double thr, int *ox, int *oz)
{
    work_seed(e, wk, seed);
    Cand cand[4];
    int fast = e->modern && (e->layers & (L_CONT | L_TEMP));
    /* 1) fast pass: few octaves, OPTIMISTIC classes (a cell counts when the
     *    missing octaves could make it match) -> an upper estimate */
    int nc = coarse_candidates(e, wk, cand, 1, 1);
    if (nc == 0) return -2.0;
    if (thr >= 0 && cand[0].sc < thr - e->margin) return -2.0;
    /* 2) full octaves at the coarse points: where to look (and, for drawings
     *    of <= 32 cells, s = 1: this IS the exact score of every offset) */
    if (fast) nc = coarse_candidates(e, wk, cand, 4, 0);
    else      nc = coarse_candidates(e, wk, cand, 4, 1);  /* biomes: already exact samples */
    if (nc == 0) return -1.0;
    if (thr >= 0 && cand[0].sc < thr - e->margin) return -1.0;
    /* 3) exact score (branch and bound) around the candidates */
    double best = -1.0; int bx = 0, bz = 0;
    int ncheck = (e->s == 1 && (e->coarse_bio || !(e->layers & L_BIO))) ? 1 : nc;
    for (int q = 0; q < ncheck; q++) {
        if (q > 0 && cand[q].sc < cand[0].sc - e->margin) break;
        if (thr >= 0 && cand[q].sc < thr - e->margin) break;
        int cx = cand[q].dx * e->s, cz = cand[q].dz * e->s;
        int x0 = cx - e->s, x1 = cx + e->s, z0 = cz - e->s, z1 = cz + e->s;
        if (x0 < -e->r) x0 = -e->r;
        if (x1 >  e->r) x1 =  e->r;
        if (z0 < -e->r) z0 = -e->r;
        if (z1 >  e->r) z1 =  e->r;
        if (x0 > x1 || z0 > z1) continue;
        int dx, dz;
        double fl = best >= 0 ? best : (thr > 0 ? thr : 0.0);
        double sc = exact_window(e, wk, x0, x1, z0, z1, fl, &dx, &dz);
        if (sc > best) { best = sc; bx = dx; bz = dz; }
    }
    if (best < 0) return -1.0;
    *ox = bx; *oz = bz;
    return best;
}

/* ── API ────────────────────────────────────────────────────────────────── */
EXPORT void *dengine_create(const int32_t *tmpl, const float *weights, int W, int H,
                            int cell, int cx, int cz, int slide_r, int mc, int large,
                            const uint32_t *groups256, double min_score, int top_k,
                            int pre_step, int pre_oct)
{
    if (W <= 0 || H <= 0 || W > 512 || H > 512 || cell <= 0 || top_k <= 0) return NULL;
    DEng *e = (DEng *)calloc(1, sizeof(DEng));
    if (!e) return NULL;
    e->W = W; e->H = H; e->cell = cell; e->cx = cx; e->cz = cz;
    e->r = slide_r < 0 ? 0 : slide_r; e->mc = mc; e->large = large ? 1 : 0;
    e->modern = mc >= MC_1_18;
    e->min_score = min_score;
    e->pre_oct = pre_oct > 0 ? pre_oct : 8;
    /* |full - fast| bound per cell: ~ the 99.9 % quantile measured for these
     * octave counts (100 000 points, 20 seeds). The fast score is a sum over
     * hundreds of cells, so the rare larger error does not move its max:
     * 0 missed seeds over 3 600 test seeds at these values. */
    e->d_cont = e->pre_oct >= 18 ? 0.0 : e->pre_oct >= 8 ? 0.12 : e->pre_oct >= 6 ? 0.26
              : e->pre_oct >= 4 ? 0.52 : 0.70;
    e->d_temp = 0.19;
    e->cap = top_k;
    for (int i = 0; i < 256; i++) e->groups[i] = groups256 ? groups256[i] : 0u;
    e->tmpl = (int32_t *)malloc((size_t)W * H * sizeof(int32_t));
    e->w = (float *)malloc((size_t)W * H * sizeof(float));
    e->rs = (int64_t *)calloc(top_k, sizeof(int64_t));
    e->rsc = (double *)calloc(top_k, sizeof(double));
    e->rox = (int *)calloc(top_k, sizeof(int));
    e->roz = (int *)calloc(top_k, sizeof(int));
    if (!e->tmpl || !e->w || !e->rs || !e->rsc || !e->rox || !e->roz) goto fail;
    e->wsum = 0;
    for (int i = 0; i < W * H; i++) {
        int32_t c = tmpl[i];
        int l = layer_of(c);
        if (!l) c = 0;
        if (l == L_TEMP && !e->modern) c = 0;       /* no climate before 1.18 */
        e->tmpl[i] = c;
        float wv = (c && weights) ? weights[i] : (c ? 1.0f : 0.0f);
        if (wv < 0) wv = 0;
        e->w[i] = wv;
        if (c) { e->wsum += wv; e->layers |= layer_of(c); }
    }
    if (e->wsum <= 0) goto fail;
    /* painted cells in a scattered order (for the branch and bound) */
    for (int i = 0; i < W * H; i++) if (e->tmpl[i]) e->np++;
    e->ord = (int32_t *)malloc((size_t)e->np * sizeof(int32_t));
    e->ocode = (int32_t *)malloc((size_t)e->np * sizeof(int32_t));
    e->ow = (float *)malloc((size_t)e->np * sizeof(float));
    e->orem = (double *)malloc((size_t)(e->np + 1) * sizeof(double));
    if (!e->ord || !e->ocode || !e->ow || !e->orem) goto fail;
    {
        int k = 0;
        for (int j = 0; j < H; j++)
            for (int i = 0; i < W; i++)
                if (e->tmpl[j * W + i]) e->ord[k++] = i | (j << 16);
        uint64_t st = 0x9E3779B97F4A7C15ULL;          /* fixed shuffle */
        for (int q = e->np - 1; q > 0; q--) {
            st = st * 6364136223846793005ULL + 1442695040888963407ULL;
            int r = (int)((st >> 33) % (uint64_t)(q + 1));
            int32_t t = e->ord[q]; e->ord[q] = e->ord[r]; e->ord[r] = t;
        }
        for (k = 0; k < e->np; k++) {
            int p = e->ord[k], idx = (p >> 16) * W + (p & 0xffff);
            e->ocode[k] = e->tmpl[idx]; e->ow[k] = e->w[idx];
        }
        e->orem[e->np] = 0;
        for (k = e->np - 1; k >= 0; k--) e->orem[k] = e->orem[k + 1] + e->ow[k];
    }
    /* coarse drawing: s so that the coarse side is <= 32 cells */
    int big = W > H ? W : H;
    int s = pre_step > 0 ? pre_step : (big + 31) / 32;
    if (s < 1) s = 1;
    e->s = s;
    e->CW = (W + s - 1) / s; e->CH = (H + s - 1) / s;
    e->R = (e->r + s - 1) / s;
    e->ctmpl = (int32_t *)calloc((size_t)e->CW * e->CH, sizeof(int32_t));
    e->cw = (float *)calloc((size_t)e->CW * e->CH, sizeof(float));
    if (!e->ctmpl || !e->cw) goto fail;
    for (int cj = 0; cj < e->CH; cj++)
        for (int ci = 0; ci < e->CW; ci++) {
            /* weighted majority class of the block; sampled at the block's
             * centre cell (the coarse map is sampled there too) */
            int32_t codes[64]; double ws[64]; int nk = 0; double tot = 0;
            for (int j = cj * s; j < (cj + 1) * s && j < H; j++)
                for (int i = ci * s; i < (ci + 1) * s && i < W; i++) {
                    int32_t c = e->tmpl[j * W + i];
                    if (!c) continue;
                    double wv = e->w[j * W + i];
                    tot += wv;
                    int k = 0;
                    while (k < nk && codes[k] != c) k++;
                    if (k == nk && nk < 64) { codes[nk] = c; ws[nk] = 0; nk++; }
                    if (k < nk) ws[k] += wv;
                }
            if (!nk) continue;
            int bk = 0;
            for (int k = 1; k < nk; k++) if (ws[k] > ws[bk]) bk = k;
            e->ctmpl[cj * e->CW + ci] = codes[bk];
            e->cw[cj * e->CW + ci] = (float)tot;
            if (layer_of(codes[bk]) == L_BIO) e->c_bio_w += tot; else e->c_fast_w += tot;
        }
    /* the coarse drawing is sampled at block centres, the map too: shift the
     * coarse "cell" by s/2 inside each block (see coarse_candidates) */
    e->coarse_bio = (e->layers == L_BIO) || !e->modern;
    /* coarse drawing vs exact score: s = 1 -> identical; else the weight of
     * the cells that differ from their block's class, plus the sub-block
     * placement error */
    if (s == 1) e->margin = 1e-9;
    else {
        double het = 0;
        for (int j = 0; j < H; j++)
            for (int i = 0; i < W; i++) {
                int32_t c = e->tmpl[j * W + i];
                if (c && c != e->ctmpl[(j / s) * e->CW + (i / s)]) het += e->w[j * W + i];
            }
        het /= e->wsum;
        e->margin = het + 0.05;     /* measured gap: <= 0.071 on random drawings */
        if (e->margin > 0.25) e->margin = 0.25;
    }
    {   /* the coarse cells the coarse passes score */
        int nc = e->CW * e->CH, GW = e->CW + 2 * e->R;
        e->cpos = (int32_t *)malloc((size_t)nc * sizeof(int32_t));
        e->ccode = (int32_t *)malloc((size_t)nc * sizeof(int32_t));
        e->clay = (int8_t *)malloc((size_t)nc);
        e->cwt = (float *)malloc((size_t)nc * sizeof(float));
        if (!e->cpos || !e->ccode || !e->clay || !e->cwt) goto fail;
        e->ncp = 0;
        for (int cj = 0; cj < e->CH; cj++)
            for (int ci = 0; ci < e->CW; ci++) {
                int32_t c = e->ctmpl[cj * e->CW + ci];
                if (!c) continue;
                int l = layer_of(c);
                if (l == L_BIO && !e->coarse_bio) continue;
                int k = e->ncp++;
                e->cpos[k] = cj * GW + ci; e->ccode[k] = c;
                e->clay[k] = (int8_t)lidx(l); e->cwt[k] = e->cw[cj * e->CW + ci];
            }
    }
#ifdef _OPENMP
    omp_init_lock(&e->lock);
#endif
    return e;
fail:
    free(e->tmpl); free(e->w); free(e->rs); free(e->rsc); free(e->rox); free(e->roz);
    free(e->ctmpl); free(e->cw);
    free(e->ord); free(e->ocode); free(e->ow); free(e->orem);
    free(e->cpos); free(e->ccode); free(e->clay); free(e->cwt); free(e);
    return NULL;
}

EXPORT void dengine_free(void *h)
{
    DEng *e = (DEng *)h;
    if (!e) return;
#ifdef _OPENMP
    omp_destroy_lock(&e->lock);
#endif
    free(e->tmpl); free(e->w); free(e->rs); free(e->rsc); free(e->rox); free(e->roz);
    free(e->ctmpl); free(e->cw);
    free(e->ord); free(e->ocode); free(e->ow); free(e->orem);
    free(e->cpos); free(e->ccode); free(e->clay); free(e->cwt); free(e);
}

/* Tests seeds start .. start+count-1 with n_threads. Returns 1 when the whole
 * range was tested, 0 when *stop interrupted it (the range must then be
 * tested again from `start`: results are de-duplicated). */
EXPORT int dengine_run(void *h, int64_t start, int64_t count, int n_threads,
                       volatile int *stop)
{
    DEng *e = (DEng *)h;
    if (!e || count <= 0) return 1;
    int complete = 1;
#ifdef _OPENMP
    if (n_threads > 0) omp_set_num_threads(n_threads);
#endif
    #pragma omp parallel
    {
        Work *wk = work_new(e);
        int64_t lt = 0, lr = 0;
        #pragma omp for schedule(dynamic, 16)
        for (int64_t k = 0; k < count; k++) {
            if (!wk || (stop && *stop)) { complete = 0; continue; }
            uint64_t seed = (uint64_t)start + (uint64_t)k;
            lt++;
            int ox, oz;
            double thr = threshold(e);
            double sc = eval_seed(e, wk, seed, thr, &ox, &oz);
            if (sc > -1.5) lr++;                    /* passed the pre-filter */
            if (sc >= 0) add_result(e, (int64_t)seed, sc, ox, oz);
        }
        #pragma omp atomic
        e->tested += lt;
        #pragma omp atomic
        e->refined += lr;
        work_free(wk);
    }
    return complete;
}

/* Diagnostics (tests / tuning): out[0] = best few-octave coarse score,
 * out[1] = best full-octave coarse score, out[2] = exact best score. */
EXPORT void dengine_stages(void *h, int64_t seed, double *out)
{
    DEng *e = (DEng *)h;
    out[0] = out[1] = out[2] = -1;
    if (!e) return;
    Work *wk = work_new(e);
    if (!wk) return;
    Cand c[4];
    work_seed(e, wk, (uint64_t)seed);
    if (coarse_candidates(e, wk, c, 1, 1)) out[0] = c[0].sc;
    if (coarse_candidates(e, wk, c, 1, 0)) out[1] = c[0].sc;
    int dx, dz;
    out[2] = eval_seed(e, wk, (uint64_t)seed, -1.0, &dx, &dz);
    work_free(wk);
}

/* One seed, no pre-filter threshold: its best position and EXACT score
 * (the same pipeline as the search: "test a seed" = what the search sees). */
EXPORT double dengine_score_seed(void *h, int64_t seed, int *ox, int *oz)
{
    DEng *e = (DEng *)h;
    if (!e) return -1.0;
    Work *wk = work_new(e);
    if (!wk) return -1.0;
    int dx = 0, dz = 0;
    double sc = eval_seed(e, wk, (uint64_t)seed, -1.0, &dx, &dz);
    work_free(wk);
    if (ox) *ox = dx;
    if (oz) *oz = dz;
    return sc;
}

/* Per cell at a given offset: the observed code of its layer (obs, same
 * encoding as the drawing: biomes as 1000 + canonical id, "neither hot nor
 * cold" = 0) and whether it matches (0/1; 255 for unpainted cells).
 * Returns the exact score at that offset. */
EXPORT double dengine_eval_at(void *h, int64_t seed, int ox, int oz,
                              int32_t *obs, uint8_t *matched)
{
    DEng *e = (DEng *)h;
    if (!e) return -1.0;
    Work *wk = work_new(e);
    if (!wk) return -1.0;
    samp_seed(e, &wk->sp, (uint64_t)seed);
    samp_need_full(e, &wk->sp);
    int W = e->W, H = e->H, cell = e->cell;
    int x0 = e->cx - (W * cell) / 2, z0 = e->cz - (H * cell) / 2;
    double m = 0;
    for (int j = 0; j < H; j++) {
        int z = z0 + (j + oz) * cell + cell / 2;
        for (int i = 0; i < W; i++) {
            int x = x0 + (i + ox) * cell + cell / 2;
            int32_t c = e->tmpl[j * W + i];
            int l = c ? layer_of(c) : L_CONT;
            if (!c && !(e->layers & L_CONT)) l = (e->layers & L_TEMP) ? L_TEMP : L_BIO;
            int v = observe(e, &wk->sp, l, x, z, 0);
            if (obs) obs[j * W + i] = (l == L_BIO) ? DC_BIO + v : v;
            if (c) {
                int ok = match(c, v);
                if (ok) m += e->w[j * W + i];
                if (matched) matched[j * W + i] = (uint8_t)ok;
            } else if (matched) matched[j * W + i] = 255;
        }
    }
    work_free(wk);
    return e->wsum > 0 ? m / e->wsum : 0.0;
}

EXPORT int dengine_results(void *h, int64_t *seeds, double *scores, int *ox, int *oz, int max_n)
{
    DEng *e = (DEng *)h;
    if (!e) return 0;
#ifdef _OPENMP
    omp_set_lock(&e->lock);
#endif
    int n = e->n < max_n ? e->n : max_n;
    for (int i = 0; i < n; i++) {
        seeds[i] = e->rs[i]; scores[i] = e->rsc[i];
        if (ox) ox[i] = e->rox[i];
        if (oz) oz[i] = e->roz[i];
    }
#ifdef _OPENMP
    omp_unset_lock(&e->lock);
#endif
    return n;
}

/* Put back results of an earlier run (a resumed search). */
EXPORT void dengine_seed_results(void *h, const int64_t *seeds, const double *scores,
                                 const int *ox, const int *oz, int n)
{
    DEng *e = (DEng *)h;
    if (!e) return;
    for (int i = 0; i < n; i++)
        add_result(e, seeds[i], scores[i], ox ? ox[i] : 0, oz ? oz[i] : 0);
}

EXPORT void dengine_counters(void *h, int64_t *tested, int64_t *refined)
{
    DEng *e = (DEng *)h;
    if (tested) *tested = e ? e->tested : 0;
    if (refined) *refined = e ? e->refined : 0;
}

/* World corner (top-left block) of the drawing at offset (ox, oz). */
EXPORT void dengine_corner(void *h, int ox, int oz, int *wx, int *wz)
{
    DEng *e = (DEng *)h;
    if (!e) { *wx = *wz = 0; return; }
    *wx = e->cx - (e->W * e->cell) / 2 + ox * e->cell;
    *wz = e->cz - (e->H * e->cell) / 2 + oz * e->cell;
}

EXPORT int dengine_version(void) { return 1; }
