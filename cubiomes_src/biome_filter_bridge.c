/*
 * biome_filter_bridge.c
 *
 * C bridge exposing all biome-condition functions needed by SSA custom sessions:
 *
 *   bfb_check_biomes      — checkForBiomes  (F_BIOME overworld/nether/end)
 *   bfb_monte_carlo       — monteCarloBiomes (F_BIOME_SAMPLE / F_NOISE_SAMPLE)
 *   bfb_biome_centers     — getBiomeCenters  (F_BIOME_CENTER)
 *   bfb_climate_minmax    — getParaRange + f_track_minmax (F_CLIMATE_MINMAX)
 *   bfb_climate_noise     — getParaRange + f_confine      (F_CLIMATE_NOISE)
 *   bfb_height            — mapApproxHeight               (F_HEIGHT)
 *   bfb_setup_biome_filter— setupBiomeFilter wrapper
 *   bfb_sizeof_biomefilter— sizeof(BiomeFilter) for Python allocation
 *   bfb_sizeof_range      — sizeof(Range) for Python allocation
 *
 * All functions take a Generator* as opaque void* so Python never needs to
 * know the internal layout of Generator.
 */

#include "finders.h"
#include "generator.h"
#include "biomenoise.h"
#include "noise.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <float.h>

#ifdef _WIN32
#  define BFB_EXPORT __declspec(dllexport)
#else
#  define BFB_EXPORT __attribute__((visibility("default")))
#endif

/* World type of the climate evaluations below (Large Biomes = 1). They used
 * the normal world only: with Large Biomes on, the Python evaluator disagreed
 * with the C engine (audit 28/09). Set by cubiomes_wrap before each call. */
static int g_bfb_large = 0;
BFB_EXPORT void bfb_set_large(int large) { g_bfb_large = large ? 1 : 0; }

/* ── sizeof helpers ──────────────────────────────────────────────────────── */

size_t bfb_sizeof_biomefilter(void) { return sizeof(BiomeFilter); }
size_t bfb_sizeof_range(void)       { return sizeof(Range); }

/* ── setupBiomeFilter wrapper ─────────────────────────────────────────────
 * biomes_incl / biomes_excl : int arrays of biome IDs (0 = end sentinel not
 * used; pass n_incl / n_excl as explicit counts).
 * flags: 0 = default (BF_FORCED_OCEAN included internally).
 */
void bfb_setup_biome_filter(
    BiomeFilter *bf,
    int mc,
    const int *biomes_incl, int n_incl,
    const int *biomes_excl, int n_excl)
{
    setupBiomeFilter(bf, mc,
        BF_FORCED_OCEAN,
        biomes_incl, n_incl,
        biomes_excl, n_excl,
        NULL, 0);
}

/* ── checkForBiomes wrapper ───────────────────────────────────────────────
 * Fills the Range from the flat parameters (scale, x, z, w, h, y).
 * Returns 1 if biomes are present, 0 otherwise.
 * dim: 0=Overworld, -1=Nether, 1=End
 */
int bfb_check_biomes(
    void           *g,
    int             scale,
    int             rx, int rz, int rw, int rh, int ry,
    int             dim,
    uint64_t        seed,
    const BiomeFilter *bf)
{
    Range r;
    r.scale = scale;
    r.x = rx; r.z = rz;
    r.sx = rw; r.sz = rh;
    r.y = ry; r.sy = 1;
    int ok = checkForBiomes((Generator*)g, NULL, r, dim, seed, bf, NULL);
    return (ok > 0) ? 1 : 0;
}

/* ── monteCarloBiomes wrapper ─────────────────────────────────────────────
 * Implements the F_BIOME_SAMPLE logic from search.cpp.
 * biomes_incl / biomes_excl are lists of biome IDs to find/exclude.
 * out_x, out_z: center of sampled biomes (average).
 * Returns 1 if the biome coverage meets confidence, 0 otherwise.
 */

typedef struct {
    const int *incl;   int n_incl;
    const int *excl;   int n_excl;
    int64_t xsum, zsum;
    int n;
} monte_data_t;

static int monte_biome_eval(Generator *g, int scale, int x, int y, int z, void *data)
{
    (void)y;
    monte_data_t *d = (monte_data_t*)data;
    int id = getBiomeAt(g, scale, x, y, z);
    for (int i = 0; i < d->n_excl; i++)
        if (d->excl[i] == id) return 0;   /* excluded biome: fail sample */
    for (int i = 0; i < d->n_incl; i++) {
        if (d->incl[i] == id) {
            d->xsum += (int64_t)x * scale;
            d->zsum += (int64_t)z * scale;
            d->n++;
            return 1;
        }
    }
    return 0;
}

int bfb_monte_carlo_biomes(
    void        *g,
    int          scale,
    int          rx, int rz, int rw, int rh, int ry,
    uint64_t     seed,
    double       coverage,
    double       confidence,
    const int   *biomes_incl, int n_incl,
    const int   *biomes_excl, int n_excl,
    int         *out_x, int *out_z)
{
    Range r;
    r.scale = scale;
    r.x = rx; r.z = rz;
    r.sx = rw; r.sz = rh;
    r.y = ry; r.sy = 1;

    monte_data_t d;
    d.incl   = biomes_incl; d.n_incl = n_incl;
    d.excl   = biomes_excl; d.n_excl = n_excl;
    d.xsum   = 0; d.zsum = 0; d.n = 0;

    uint64_t rng = seed;
    setSeed(&rng, seed);

    /* applySeed is needed before checkForBiomes/monteCarloBiomes */
    applySeed((Generator*)g, DIM_OVERWORLD, seed);

    int ok = monteCarloBiomes((Generator*)g, r, &rng, coverage, confidence,
                               monte_biome_eval, &d);
    if (d.n > 0) {
        *out_x = (int)(d.xsum / d.n);
        *out_z = (int)(d.zsum / d.n);
    } else {
        *out_x = (rx + rw / 2) * scale;
        *out_z = (rz + rh / 2) * scale;
    }
    return (ok == 1) ? 1 : 0;
}

/* ── getBiomeCenters wrapper ──────────────────────────────────────────────
 * Returns number of centers found (up to nmax).
 * out_pos: flat int array [x0, z0, x1, z1, ...] of length 2*nmax.
 */
int bfb_get_biome_centers(
    void       *g,
    int         rx, int rz, int rw, int rh, int ry,
    int         biome_id,
    int         min_size,
    int         tolerance,
    int        *out_pos,
    int         nmax)
{
    Range r;
    r.scale = 4;       /* getBiomeCenters requires scale=4 */
    r.x = rx; r.z = rz;
    r.sx = rw; r.sz = rh;
    r.y = ry; r.sy = 1;

    Pos *pos_buf = (Pos*)malloc(sizeof(Pos) * nmax);
    if (!pos_buf) return -1;

    int n = getBiomeCenters(pos_buf, NULL, nmax,
                             (Generator*)g, r, biome_id, min_size, tolerance, NULL);
    for (int i = 0; i < n && i < nmax; i++) {
        out_pos[i * 2]     = pos_buf[i].x;
        out_pos[i * 2 + 1] = pos_buf[i].z;
    }
    free(pos_buf);
    return n;
}

/* ── getParaRange helpers (F_CLIMATE_MINMAX) ──────────────────────────────
 * Replicates f_track_minmax from search.cpp.
 */
typedef struct {
    double vmin, vmax;
    int xmin, zmin, xmax, zmax;
} track_mm_t;

static int f_track_minmax(void *data, int x, int z, double p)
{
    track_mm_t *t = (track_mm_t*)data;
    if (p < t->vmin) { t->vmin = p; t->xmin = x; t->zmin = z; }
    if (p > t->vmax) { t->vmax = p; t->xmax = x; t->zmax = z; }
    return 0;
}

/*
 * bfb_climate_minmax
 *
 * Exact port of F_CLIMATE_MINMAX from search.cpp.
 *
 * para   : NP_* constant (0=Temp, 1=Humid, 2=Cont, 3=Erosion, 5=Weird)
 * mode   : bitmask  bit0=LOCATE_MIN, bit1=LOCATE_MAX, bit2=TEST_LOWER, bit3=TEST_UPPER
 * vmin   : lower bound (only used when TEST_LOWER set)
 * vmax   : upper bound (only used when TEST_UPPER set)
 * nmax   : octave limit (-1 = all)
 * invert : 0 = normal, 1 = invert test
 *
 * out_x, out_z : resolved position of located extreme (block coords)
 * out_val      : actual extreme value found
 *
 * Returns 1 if the condition passes, 0 if it fails.
 *
 * NOTE: BiomeNoise is initialised inside this function from seed + para.
 * The Generator state is not modified.
 */
int bfb_climate_minmax(
    uint64_t seed,
    int      para,
    int      rx, int rz, int rw, int rh,
    int      mode,
    double   vmin, double vmax,
    int      nmax,
    int      invert,
    int     *out_x, int *out_z,
    double  *out_val)
{
    if (para < 0 || para >= NP_MAX)
        return 0;

    BiomeNoise bn;
    memset(&bn, 0, sizeof(bn));
    setClimateParaSeed(&bn, seed, g_bfb_large, para, nmax);

    track_mm_t info;
    info.vmin = +1e30; info.vmax = -1e30;
    info.xmin = rx;    info.zmin = rz;
    info.xmax = rx;    info.zmax = rz;

    double p_lo = -1e30, p_hi = 1e30;
    double *p_min = (mode & 1) ? &p_lo : NULL;   /* LOCATE_MIN */
    double *p_max = (mode & 2) ? &p_hi : NULL;   /* LOCATE_MAX */

    getParaRange(&bn.climate[para], p_min, p_max,
                  rx, rz, rw, rh, &info, f_track_minmax);

    /* TEST_LOWER / TEST_UPPER bounds */
    double lo = (mode & 4) ? vmin : -1e30;
    double hi = (mode & 8) ? vmax : +1e30;

    double evalmin = (mode & 1) ? info.vmin : info.vmax;
    double evalmax = (mode & 2) ? info.vmax : info.vmin;

    int pass;
    if (invert)
        pass = !(evalmin > lo && evalmax < hi);
    else
        pass = (evalmin >= lo && evalmax <= hi);

    /* Write located position */
    if (mode & 1) {
        *out_x = info.xmin << 2;
        *out_z = info.zmin << 2;
        *out_val = info.vmin;
    } else {
        *out_x = info.xmax << 2;
        *out_z = info.zmax << 2;
        *out_val = info.vmax;
    }
    return pass ? 1 : 0;
}

/* ── F_CLIMATE_NOISE helper (f_confine) ──────────────────────────────────
 * Checks multiple climate parameters against include/exclude ranges.
 *
 * para_ok_lo[i], para_ok_hi[i]  : required range (INT_MIN/MAX = unconstrained)
 * para_ex_lo[i], para_ex_hi[i]  : exclusion range (INT_MIN/MAX = unconstrained)
 *
 * Returns 1 if all active parameters pass, 0 otherwise.
 * Mirrors the inner loop of F_CLIMATE_NOISE in search.cpp exactly.
 */

typedef struct { double lo, hi; } confine_t;

static int f_confine(void *data, int x, int z, double p)
{
    (void)x; (void)z;
    confine_t *c = (confine_t*)data;
    return (p < c->lo || p > c->hi) ? 1 : 0;
}

int bfb_climate_noise(
    uint64_t    seed,
    int         rx, int rz, int rw, int rh,
    /* per-NP arrays (NP_MAX = 6 entries) */
    const int  *ok_lo,   /* required min (raw int × 10000) */
    const int  *ok_hi,   /* required max */
    const int  *ex_lo,   /* exclusion min */
    const int  *ex_hi,   /* exclusion max */
    int         nmax)
{
    /* Evaluation order from search.cpp for speed */
    static const int order[] = {
        NP_TEMPERATURE, NP_HUMIDITY, NP_WEIRDNESS,
        NP_EROSION,     NP_CONTINENTALNESS,
    };
    int n_order = (int)(sizeof(order) / sizeof(order[0]));

    for (int j = 0; j < n_order; j++) {
        int i = order[j];
        if (i >= NP_MAX) continue;
        /* Skip unconstrained parameters */
        int INT_MIN_V = -2147483647 - 1;
        int INT_MAX_V =  2147483647;
        if (ok_lo[i] == INT_MIN_V && ok_hi[i] == INT_MAX_V &&
            ex_lo[i] == INT_MIN_V && ex_hi[i] == INT_MAX_V)
            continue;

        BiomeNoise bn;
        memset(&bn, 0, sizeof(bn));
        setClimateParaSeed(&bn, seed, g_bfb_large, i, nmax);

        confine_t conf;
        conf.lo = (double)ex_lo[i];
        conf.hi = (double)ex_hi[i];

        double pmin = +1e30, pmax = -1e30;
        int err = getParaRange(&bn.climate[i], &pmin, &pmax,
                                rx, rz, rw, rh, &conf, f_confine);
        if (err) return 0;   /* parameter is outside exclusion range */

        /* Check required range */
        if (pmin > (double)ok_hi[i] || pmax < (double)ok_lo[i]) return 0;
        /* Check exclusion range */
        if (pmin < (double)ex_lo[i] || pmax > (double)ex_hi[i]) return 0;
    }
    return 1;
}

/* ── bfb_noise_sample — point sample (F_NOISE_SAMPLE) ───────────────────
 *
 * Samples a single climate parameter at an exact (x, z) block position and
 * checks whether the value is within [vmin, vmax].
 *
 * para     : NP_* constant (0=Temp, 1=Humid, 2=Cont, 3=Erosion, 5=Weird)
 * bx, bz   : block coordinates of the sample point
 * nmax     : octave limit (-1 = all)
 * vmin     : lower bound (raw int × 10000, INT_MIN = no lower bound)
 * vmax     : upper bound (raw int × 10000, INT_MAX = no upper bound)
 * invert   : 0=normal (pass if in range), 1=invert (pass if out of range)
 * out_val  : if non-NULL, receives the raw sampled value × 10000 (as int)
 *
 * Returns 1 if the condition passes, 0 if it fails.
 *
 * Coordinate convention (matches cubiomes-viewer F_NOISE_SAMPLE):
 *   noise_x = bx / 4.0,  noise_z = bz / 4.0
 */
int bfb_noise_sample(
    uint64_t seed,
    int      para,
    int      bx, int bz,
    int      nmax,
    int      vmin_raw, int vmax_raw,
    int      invert,
    int     *out_val)
{
    if (para < 0 || para >= NP_MAX)
        return 0;

    int INT_MIN_V = -2147483647 - 1;
    int INT_MAX_V =  2147483647;

    BiomeNoise bn;
    memset(&bn, 0, sizeof(bn));
    setClimateParaSeed(&bn, seed, g_bfb_large, para, nmax);

    /* sampleClimatePara expects noise-space coords: block / 4 */
    double nx = bx / 4.0;
    double nz = bz / 4.0;
    double val = sampleClimatePara(&bn, NULL, nx, nz);

    /* cubiomes stores climate values as int × 10000 internally */
    int raw = (int)(val * 10000.0 + (val >= 0.0 ? 0.5 : -0.5));

    if (out_val)
        *out_val = raw;

    int lo_ok = (vmin_raw == INT_MIN_V) || (raw >= vmin_raw);
    int hi_ok = (vmax_raw == INT_MAX_V) || (raw <= vmax_raw);
    int pass  = lo_ok && hi_ok;

    return invert ? !pass : pass;
}

/* ── mapApproxHeight wrapper (F_HEIGHT) ──────────────────────────────────
 * rx, rz : position in scale-4 coords (block / 4).
 * Returns the approximate height (float) at that point.
 * The Generator must already be seeded for the overworld.
 */
float bfb_approx_height(void *g, uint64_t seed, int rx, int rz)
{
    SurfaceNoise sn;
    initSurfaceNoise(&sn, DIM_OVERWORLD, seed);
    float y = 0.0f;
    mapApproxHeight(&y, NULL, (Generator*)g, &sn, rx, rz, 1, 1);
    return y;
}
