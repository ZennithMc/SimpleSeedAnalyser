/*
 * terrain116.c — the exact Java 1.16 overworld terrain (10/10).
 *
 * A bit-for-bit port of Minecraft 1.16.1 SurfaceChunkGenerator (noise: the
 * stone / water / air of the NOISE status), then the surface blocks, bedrock
 * and the cave / ravine carvers (see the parts below). Every float / double
 * operation follows the Java order: compile with -ffp-contract=off (no FMA) and
 * SSE maths (no x87 excess precision).
 *
 * Biomes: cubiomes' 1:4 layer (the noise's getBiomeForNoiseGen); biome depth /
 * scale: the exact Java floats (t116_bds.h, dumped from the 1.16.1 registry).
 * Structure "beards" (villages / outposts raising the ground) are NOT applied.
 */
#pragma GCC optimize ("fp-contract=off")   /* Java float/double order: no FMA */
#include "terrain116.h"

#include <math.h>
#include <string.h>
#include <stdlib.h>

#include "t116_bds.h"
#include "layers.h"
#include "mineshaft116.h"

/* ── Java helpers ─────────────────────────────────────────────────────────── */
static inline long long j_lfloor(double d) { long long l = (long long)d; return d < (double)l ? l - 1 : l; }
static inline int j_floor(double d) { int i = (int)d; return d < (double)i ? i - 1 : i; }
static inline double j_mp(double d) { return d - (double)j_lfloor(d / 3.3554432E7 + 0.5) * 3.3554432E7; }
static inline double j_lerp(double t, double a, double b) { return a + t * (b - a); }
static inline double j_clampedLerp(double a, double b, double t)
{ return t < 0.0 ? a : (t > 1.0 ? b : j_lerp(t, a, b)); }

static float T116_KERNEL[25];
static int T116_KINIT = 0;

static void t116_kernel(void)
{
    if (T116_KINIT) return;
    for (int i = -2; i <= 2; ++i)
        for (int j = -2; j <= 2; ++j) {
            float f = (float)(i * i + j * j) + 0.2f;
            float s = (float)sqrt((double)f);
            T116_KERNEL[i + 2 + (j + 2) * 5] = 10.0f / s;
        }
    T116_KINIT = 1;
}

static void t116_post_init(T116 *t);

static void t116_static(void);
static void sint_init(void);
static char T116_LOCK = 0;

int t116_init(T116 *t, int mc, uint64_t seed)
{
    memset(t, 0, sizeof(*t));
    /* the shared tables, once (threads may call this together) */
    while (__atomic_test_and_set(&T116_LOCK, __ATOMIC_ACQUIRE)) { }
    t116_kernel();
    t116_static();
    sint_init();
    __atomic_clear(&T116_LOCK, __ATOMIC_RELEASE);
    t->mc = mc;
    t->seed = seed;
    setupGenerator(&t->g, mc, 0);
    applySeed(&t->g, DIM_OVERWORLD, seed);
    initSurfaceNoise(&t->sn, DIM_OVERWORLD, seed);
    t116_post_init(t);
    return 1;
}

/* SurfaceChunkGenerator.sampleNoise (one noise cell, the 3 octave sets).
   The three sums are independent: the interpolation sum is done first and the
   min / max sums only when clampedLerp needs them (same values, fewer samples). */
static double t116_sample_noise_raw(const T116 *t, int x, int y, int z);

/* (10/10) the three octave sums only depend on the 48 low bits of the seed
 * (the Perlin octaves come from java.util.Random(seed)), not on the biomes:
 * the 65 536 sister seeds of a structure seed share them. Cached per thread,
 * for the running 48-bit value (a generation number invalidates the table
 * when it changes, no clearing). Same function, same doubles: exact. */
#define T116_NC_BITS 16
typedef struct { uint64_t pos; uint32_t gen; uint32_t pad; double v; } T116NoiseSlot;
static __thread T116NoiseSlot *t116_nc = NULL;
static __thread uint64_t t116_nc_low = ~0ULL;
static __thread uint32_t t116_nc_gen = 0;

static double t116_sample_noise(const T116 *t, int x, int y, int z)
{
    if (x < -(1 << 23) || x >= (1 << 23) || z < -(1 << 23) || z >= (1 << 23) || y < 0 || y > 255)
        return t116_sample_noise_raw(t, x, y, z);
    if (!t116_nc) {
        t116_nc = (T116NoiseSlot *)calloc((size_t)1 << T116_NC_BITS, sizeof(T116NoiseSlot));
        if (!t116_nc) return t116_sample_noise_raw(t, x, y, z);
    }
    uint64_t low = t->seed & 0xFFFFFFFFFFFFULL;
    if (low != t116_nc_low) {
        t116_nc_low = low;
        if (++t116_nc_gen == 0) {          /* wrapped: really clear */
            memset(t116_nc, 0, sizeof(T116NoiseSlot) << T116_NC_BITS);
            t116_nc_gen = 1;
        }
    }
    uint64_t pos = ((uint64_t)(uint32_t)(x & 0xFFFFFF)) | ((uint64_t)(uint32_t)(z & 0xFFFFFF) << 24) |
                   ((uint64_t)(uint32_t)y << 48);
    uint64_t h = pos * 0x9E3779B97F4A7C15ULL;
    uint32_t i = (uint32_t)(h >> (64 - T116_NC_BITS));
    T116NoiseSlot *sl = &t116_nc[i];
    if (sl->gen == t116_nc_gen && sl->pos == pos) return sl->v;
    double v = t116_sample_noise_raw(t, x, y, z);
    sl->pos = pos; sl->gen = t116_nc_gen; sl->v = v;
    return v;
}

static double t116_sample_noise_raw(const T116 *t, int x, int y, int z)
{
    const double hs = 684.412 * 0.9999999814507745, vs = 684.412 * 0.9999999814507745;
    const double hst = hs / 80.0, vst = vs / 160.0;
    double d = 0.0, e = 0.0, f = 0.0, g = 1.0;
    for (int i = 0; i < 8; ++i) {
        f += samplePerlin(&t->sn.octmain.octaves[i],
                          j_mp((double)x * hst * g), j_mp((double)y * vst * g), j_mp((double)z * hst * g),
                          vst * g, (double)y * vst * g) / g;
        g /= 2.0;
    }
    double tt = (f / 10.0 + 1.0) / 2.0;
    int need_d = !(tt > 1.0), need_e = !(tt < 0.0);
    g = 1.0;
    for (int i = 0; i < 16; ++i) {
        double h = j_mp((double)x * hs * g);
        double j = j_mp((double)y * vs * g);
        double k = j_mp((double)z * hs * g);
        double l = vs * g;
        if (need_d) d += samplePerlin(&t->sn.octmin.octaves[i], h, j, k, l, (double)y * l) / g;
        if (need_e) e += samplePerlin(&t->sn.octmax.octaves[i], h, j, k, l, (double)y * l) / g;
        g /= 2.0;
    }
    return j_clampedLerp(d / 512.0, e / 512.0, tt);
}

/* method_28553: the random density offset of a column */
static double t116_density_offset(const T116 *t, int x, int z)
{
    const OctaveNoise *on = &t->sn.octdepth;
    double v = 0.0;
    double g = 1.0;                       /* field_20660 = 2^0 */
    double h = 1.0 / (65536.0 - 1.0);     /* field_20659 */
    double px = (double)(int)(x * 200), pz = (double)(int)(z * 200);
    for (int i = 0; i < on->octcnt; ++i) {
        const PerlinNoise *p = on->octaves + i;
        v += samplePerlin(p, j_mp(px * g), -p->b, j_mp(pz * g), 1.0 * g, 0.0 * g) * h;
        g /= 2.0;
        h *= 2.0;
    }
    double f = v < 0.0 ? -v * 0.3 : v;
    double gg = f * 24.575625 - 2.0;
    if (gg < 0.0) return gg * 0.009486607142857142;
    return (gg < 1.0 ? gg : 1.0) * 0.006640625;
}

static inline float bdepth(int id) { return (id >= 0 && id < 256) ? T116_BDS[id][0] : 0.1f; }
static inline float bscale(int id) { return (id >= 0 && id < 256) ? T116_BDS[id][1] : 0.2f; }

/* sampleNoiseColumn, split in two (10/10 bis): the column constants, then
   one cell. bio = the 5×5 1:4 biomes around (x, z), bio[(n+2)*5 + (m+2)] */
typedef struct { double ac, ad, ao; } T116ColK;

static void t116_colk(const T116 *t, int x, int z, const int *bio, T116ColK *k)
{
    float g = 0.0f, h = 0.0f, i = 0.0f;
    float l = bdepth(bio[12]);
    for (int m = -2; m <= 2; ++m)
        for (int n = -2; n <= 2; ++n) {
            int id = bio[(n + 2) * 5 + (m + 2)];
            float o = bdepth(id), p = bscale(id);
            float u = o > l ? 0.5f : 1.0f;
            float v = u * T116_KERNEL[m + 2 + (n + 2) * 5] / (o + 2.0f);
            g += p * v;
            h += o * v;
            i += v;
        }
    float w = h / i;
    float y = g / i;
    double aa = (double)(w * 0.5f - 0.125f);
    double ab = (double)(y * 0.9f + 0.1f);
    k->ac = aa * 0.265625;
    k->ad = 96.0 / ab;
    k->ao = t116_density_offset(t, x, z);
}

/* the cell value from its 3-octave-set noise value `as` */
static inline double t116_cellf(const T116ColK *k, int ar, double as)
{
    double at = 1.0 - (double)ar * 2.0 / 32.0 + k->ao;
    double au = at * 1.0 + -0.46875;
    double av = (au + k->ac) * k->ad;
    as = av > 0.0 ? as + av * 4.0 : as + av;
    double aw = ((double)(32 - ar) - 0.0) / 3.0;
    return j_clampedLerp(-10.0, as, aw);
}

/* |sampleNoise| <= 65535 * 2 / 512 < 256: a Perlin sample is a blend of
   gradient dots |g·d| <= 2, the octave sums weigh them 1, 2 … 32768. The cell
   value grows with the noise: f(-256) .. f(256) bound it without sampling. */
#define T116_NB 256.0

void t116_column_b(const T116 *t, int x, int z, const int *bio, double out[33])
{
    T116ColK k;
    t116_colk(t, x, z, bio, &k);
    for (int ar = 0; ar <= 32; ++ar)
        out[ar] = t116_cellf(&k, ar, t116_sample_noise(t, x, ar, z));
}

void t116_column(const T116 *t, int x, int z, double out[33])
{
    int bio[25];
    Range r = {4, x - 2, z - 2, 5, 5, 0, 1};
    int *c = allocCache(&t->g, r);
    genBiomes(&t->g, c, r);
    memcpy(bio, c, sizeof(bio));
    free(c);
    t116_column_b(t, x, z, bio, out);
}

/* a thread-local direct-mapped cache of noise columns: neighbouring chunks
   share their edge columns */
#define T116_CCACHE 4096
/* (10/10 bis) a noise column, lazily: the bounds of every cell first, the
   exact value of a cell only when a block's sign needs it */
typedef struct {
    uint64_t seed; int x, z, ok, mc;
    T116ColK k;
    uint64_t known;
    double v[33], lo[33], hi[33];
} T116Col;
static __thread T116Col *T116_CC = NULL;

static void col_init(const T116 *t, int x, int z, const int *bio25, T116Col *c)
{
    c->seed = t->seed; c->mc = t->mc; c->x = x; c->z = z; c->ok = 1; c->known = 0;
    t116_colk(t, x, z, bio25, &c->k);
    for (int ar = 0; ar <= 32; ++ar) {
        c->lo[ar] = t116_cellf(&c->k, ar, -T116_NB);
        c->hi[ar] = t116_cellf(&c->k, ar, T116_NB);
    }
}

/* the thread's cache entry of column (x, z) (filled with bounds), copied to *out */
static void t116_column_lazy(const T116 *t, int x, int z, const int *bio25, T116Col *out)
{
    if (!T116_CC) T116_CC = calloc(T116_CCACHE, sizeof(T116Col));
    if (T116_CC) {
        uint32_t h = ((uint32_t)x * 0x9E3779B1u) ^ ((uint32_t)z * 0x85EBCA77u);
        T116Col *c = &T116_CC[(h ^ (h >> 15)) & (T116_CCACHE - 1)];
        if (!(c->ok && c->seed == t->seed && c->mc == t->mc && c->x == x && c->z == z))
            col_init(t, x, z, bio25, c);
        *out = *c;
        return;
    }
    col_init(t, x, z, bio25, out);
}

static inline double col_val(const T116 *t, T116Col *c, int ar)
{
    if (!((c->known >> ar) & 1)) {
        c->v[ar] = t116_cellf(&c->k, ar, t116_sample_noise(t, c->x, ar, c->z));
        c->known |= 1ULL << ar;
        if (T116_CC) {                       /* back to the cache when still there */
            uint32_t h = ((uint32_t)c->x * 0x9E3779B1u) ^ ((uint32_t)c->z * 0x85EBCA77u);
            T116Col *e = &T116_CC[(h ^ (h >> 15)) & (T116_CCACHE - 1)];
            if (e->ok && e->seed == c->seed && e->mc == c->mc && e->x == c->x && e->z == c->z) {
                e->v[ar] = c->v[ar];
                e->known |= 1ULL << ar;
            }
        }
    }
    return c->v[ar];
}

static void t116_column_cached(const T116 *t, int x, int z, const int *bio25, double out[33])
{
    T116Col c;
    t116_column_lazy(t, x, z, bio25, &c);
    for (int ar = 0; ar <= 32; ++ar) out[ar] = col_val(t, &c, ar);
}

/* populateNoise for one chunk: out[(y*16 + z)*16 + x] = T116_AIR / STONE / WATER */
static void t116_noise(const T116 *t, int cx, int cz, const int *bio, uint8_t *out)
{
    static __thread T116Col col[5][5];
    for (int a = 0; a < 5; ++a)
        for (int b = 0; b < 5; ++b) {
            int bb[25];
            for (int n = 0; n < 5; ++n)
                for (int m = 0; m < 5; ++m)
                    bb[n * 5 + m] = bio[(b + n) * 9 + (a + m)];
            t116_column_lazy(t, cx * 4 + a, cz * 4 + b, bb, &col[a][b]);
        }
    const double EPS = 1e-6;
    for (int n = 0; n < 4; ++n)
        for (int p = 0; p < 4; ++p)
            for (int q = 31; q >= 0; --q) {
                T116Col *C4[4] = {&col[n][p], &col[n][p + 1], &col[n + 1][p], &col[n + 1][p + 1]};
                double mlo = 1e300, mhi = -1e300;
                for (int k = 0; k < 4; ++k)
                    for (int dq = 0; dq < 2; ++dq) {
                        if (C4[k]->lo[q + dq] < mlo) mlo = C4[k]->lo[q + dq];
                        if (C4[k]->hi[q + dq] > mhi) mhi = C4[k]->hi[q + dq];
                    }
                if (mlo > EPS || mhi < -EPS) {      /* the whole cell has one sign */
                    for (int u = 0; u < 8; ++u) {
                        int v = q * 8 + u;
                        uint8_t blk = mlo > EPS ? T116_STONE : (v < 63 ? T116_WATER : T116_AIR);
                        for (int dz = 0; dz < 4; ++dz)
                            memset(&out[(v * 16 + (p * 4 + dz)) * 16 + n * 4], blk, 4);
                    }
                    continue;
                }
                double d = col_val(t, C4[0], q), e = col_val(t, C4[1], q), f = col_val(t, C4[2], q), g = col_val(t, C4[3], q);
                double h = col_val(t, C4[0], q + 1), rr = col_val(t, C4[1], q + 1);
                double s = col_val(t, C4[2], q + 1), tt = col_val(t, C4[3], q + 1);
                for (int u = 7; u >= 0; --u) {
                    int v = q * 8 + u;
                    double y = (double)u / 8.0;
                    double z = j_lerp(y, d, h), aa = j_lerp(y, f, s), ab = j_lerp(y, e, rr), ac = j_lerp(y, g, tt);
                    for (int ad = 0; ad < 4; ++ad) {
                        double ag = (double)ad / 4.0;
                        double ah = j_lerp(ag, z, aa), ai = j_lerp(ag, ab, ac);
                        for (int aj = 0; aj < 4; ++aj) {
                            double am = (double)aj / 4.0;
                            double an = j_lerp(am, ah, ai);
                            double ao = an / 200.0;
                            ao = ao < -1.0 ? -1.0 : (ao > 1.0 ? 1.0 : ao);
                            ao = ao / 2.0 - ao * ao * ao / 24.0;
                            uint8_t blk = ao > 0.0 ? T116_STONE : (v < 63 ? T116_WATER : T116_AIR);
                            out[(v * 16 + (p * 4 + aj)) * 16 + (n * 4 + ad)] = blk;
                        }
                    }
                }
            }
}

/* ══ simplex noise (Java SimplexNoiseSampler / OctaveSimplexNoiseSampler) ═══ */
static double S3, SKEW2, UNSKEW2;
static const int SGRAD[16][3] = {{1,1,0},{-1,1,0},{1,-1,0},{-1,-1,0},{1,0,1},{-1,0,1},{1,0,-1},{-1,0,-1},
                                 {0,1,1},{0,-1,1},{0,1,-1},{0,-1,-1},{1,1,0},{0,-1,1},{-1,1,0},{0,-1,-1}};

static void simplex_init(T116Simplex *s, uint64_t *r)
{
    s->ox = nextDouble(r) * 256.0;
    s->oy = nextDouble(r) * 256.0;
    s->oz = nextDouble(r) * 256.0;
    for (int i = 0; i < 256; ++i) s->p[i] = (uint8_t)i;
    for (int j = 0; j < 256; ++j) {
        int k = nextInt(r, 256 - j);
        uint8_t l = s->p[j];
        s->p[j] = s->p[k + j];
        s->p[k + j] = l;
    }
}

static inline double sgrad(int h, double x, double y, double z, double d)
{
    double e = d - x * x - y * y - z * z;
    if (e < 0.0) return 0.0;
    e *= e;
    return e * e * ((double)SGRAD[h][0] * x + (double)SGRAD[h][1] * y + (double)SGRAD[h][2] * z);
}

static double simplex2(const T116Simplex *s, double x, double y)
{
    double d = (x + y) * SKEW2;
    int i = j_floor(x + d);
    int j = j_floor(y + d);
    double e = (double)(i + j) * UNSKEW2;
    double f = (double)i - e;
    double g = (double)j - e;
    double h = x - f;
    double k = y - g;
    int n, o;
    if (h > k) { n = 1; o = 0; } else { n = 0; o = 1; }
    double p = h - (double)n + UNSKEW2;
    double q = k - (double)o + UNSKEW2;
    double r = h - 1.0 + 2.0 * UNSKEW2;
    double ss = k - 1.0 + 2.0 * UNSKEW2;
    int t = i & 0xFF, u = j & 0xFF;
#define GG(hh) (s->p[(hh) & 0xFF])
    int v = GG(t + GG(u)) % 12;
    int w = GG(t + n + GG(u + o)) % 12;
    int z = GG(t + 1 + GG(u + 1)) % 12;
#undef GG
    double aa = sgrad(v, h, k, 0.0, 0.5);
    double ab = sgrad(w, p, q, 0.0, 0.5);
    double ac = sgrad(z, r, ss, 0.0, 0.5);
    return 70.0 * (aa + ab + ac);
}

/* octaves -(n-1)..0 from a Java Random */
static void octsimplex_init(T116OctSimplex *o, uint64_t *r, int n)
{
    o->n = n;
    for (int i = 0; i < n; ++i) simplex_init(&o->s[i], r);
    o->lac0 = 1.0;
    o->amp0 = 1.0 / (pow(2.0, n) - 1.0);
}

static double octsimplex(const T116OctSimplex *o, double x, double y, int bl)
{
    double d = 0.0, e = o->lac0, f = o->amp0;
    for (int i = 0; i < o->n; ++i) {
        const T116Simplex *s = &o->s[i];
        d += simplex2(s, x * e + (bl ? s->ox : 0.0), y * e + (bl ? s->oy : 0.0)) * f;
        e /= 2.0;
        f *= 2.0;
    }
    return d;
}

static T116OctSimplex FOLIAGE, FROZEN_T;   /* Biome.FOLIAGE_NOISE (2345), FrozenOceanBiome (3456) */
static int STATIC_OK = 0;

static void t116_static(void)
{
    if (STATIC_OK) return;
    S3 = sqrt(3.0);
    SKEW2 = 0.5 * (S3 - 1.0);
    UNSKEW2 = (3.0 - S3) / 6.0;
    uint64_t r;
    setSeed(&r, 2345);
    octsimplex_init(&FOLIAGE, &r, 1);
    setSeed(&r, 3456);
    octsimplex_init(&FROZEN_T, &r, 3);
    STATIC_OK = 1;
}

/* the badlands terracotta bands (BadlandsSurfaceBuilder.initLayerBlocks) */
static void badlands_init(T116 *t)
{
    uint64_t r;
    uint8_t *L = t->bl_layers;
    setSeed(&r, t->seed);
    octsimplex_init(&t->bl_layer, &r, 1);
    for (int i = 0; i < 64; ++i) L[i] = T116_TERRACOTTA;
    for (int i = 0; i < 64; ++i) {
        if ((i += nextInt(&r, 5) + 1) >= 64) continue;
        L[i] = T116_ORANGE_T;
    }
    int j = nextInt(&r, 4) + 2;
    for (int k = 0; k < j; ++k) {
        int l = nextInt(&r, 3) + 1, m = nextInt(&r, 64);
        for (int n = 0; m + n < 64 && n < l; ++n) L[m + n] = T116_YELLOW_T;
    }
    int o = nextInt(&r, 4) + 2;
    for (int p = 0; p < o; ++p) {
        int q = nextInt(&r, 3) + 2, rr = nextInt(&r, 64);
        for (int s = 0; rr + s < 64 && s < q; ++s) L[rr + s] = T116_BROWN_T;
    }
    int tt = nextInt(&r, 4) + 2;
    for (int u = 0; u < tt; ++u) {
        int v = nextInt(&r, 3) + 1, w = nextInt(&r, 64);
        for (int x = 0; w + x < 64 && x < v; ++x) L[w + x] = T116_RED_T;
    }
    int y = nextInt(&r, 3) + 3;
    int z = 0;
    for (int aa = 0; aa < y; ++aa) {
        z += nextInt(&r, 16) + 4;          /* once per band (CFR folded it into the loop test) */
        for (int ac = 0; z + ac < 64 && ac < 1; ++ac) {
            L[z + ac] = T116_WHITE_T;
            if (z + ac > 1 && (next(&r, 1) != 0)) L[z + ac - 1] = T116_LIGHT_GRAY_T;
            if (z + ac >= 63 || !(next(&r, 1) != 0)) continue;
            L[z + ac + 1] = T116_LIGHT_GRAY_T;
        }
    }
    setSeed(&r, t->seed);
    octsimplex_init(&t->bl_cut, &r, 4);
    octsimplex_init(&t->bl_height, &r, 1);
    setSeed(&r, t->seed);
    octsimplex_init(&t->fo_a, &r, 4);
    octsimplex_init(&t->fo_b, &r, 1);
}

static void t116_post_init(T116 *t)
{
    t116_static();
    /* surfaceDepthNoise: the 4 samplers after the 3 octave perlins — same
       random draws as cubiomes' octsurf perlins */
    t->surf.n = 4;
    t->surf.lac0 = 1.0;
    t->surf.amp0 = 1.0 / 15.0;
    for (int i = 0; i < 4; ++i) {
        const PerlinNoise *p = &t->sn.octsurf.octaves[i];
        t->surf.s[i].ox = p->a;
        t->surf.s[i].oy = p->b;
        t->surf.s[i].oz = p->c;
        memcpy(t->surf.s[i].p, p->d, 256);
    }
    badlands_init(t);
}

/* ══ surface (SurfaceChunkGenerator.buildSurface + the biome surface builders) ══ */
#include "t116_biomes.h"

static inline const T116Biome *tb(int id)
{
    static const T116Biome def = {0, T116_GRASS, T116_DIRT, T116_GRAVEL, 0.5f, 0, 1};
    return (id >= 0 && id < 256 && T116_BIOMES[id].ok) ? &T116_BIOMES[id] : &def;
}

static float biome_temp(const T116Biome *b, int x, int z)
{
    /* y <= 64 only (the surface builders never ask higher) */
    float f = b->temp;
    if (b->flags & 1) {
        double d = octsimplex(&FROZEN_T, (double)x * 0.05, (double)z * 0.05, 0) * 7.0;
        double e = octsimplex(&FOLIAGE, (double)x * 0.2, (double)z * 0.2, 0);
        double g = d + e;
        if (g < 0.3 && octsimplex(&FOLIAGE, (double)x * 0.09, (double)z * 0.09, 0) < 0.8)
            f = 0.2f;
    }
    return f;
}

static inline int is_terracotta(int b)
{
    return b >= T116_TERRACOTTA && b <= T116_LIGHT_GRAY_T;
}
static inline int is_colored_t(int b) { return b >= T116_WHITE_T && b <= T116_LIGHT_GRAY_T; }

#define B(xx, yy, zz) T116_AT(c, xx, yy, zz)

/* (10/10) getBiomeAt(g, 4, x, 0, z), memoised per thread: one point costs
 * ~250 k instructions in the 1.16 layer stack and the same start / carver
 * chunks are asked again and again (mineshaft starts: ~10x per seed). The
 * key holds the whole generator state that matters (seed, version, flags,
 * dimension): same answer as getBiomeAt. */
typedef struct { uint64_t seed; int32_t x, z, mc, dim; uint32_t flags; int32_t b; } T116B4;
static __thread T116B4 *t116_b4 = NULL;
int t116_biome4(const Generator *g, int x, int z)
{
    if (!t116_b4) {
        t116_b4 = (T116B4 *)calloc(4096, sizeof(T116B4));
        if (!t116_b4) return getBiomeAt(g, 4, x, 0, z);
    }
    uint64_t h = (g->seed ^ ((uint64_t)(uint32_t)x * 0x9E3779B97F4A7C15ULL) ^
                  ((uint64_t)(uint32_t)z * 0xC2B2AE3D27D4EB4FULL)) * 0x165667B19E3779F9ULL;
    T116B4 *e = &t116_b4[h >> 52];
    if (e->b > 0 && e->seed == g->seed && e->x == x && e->z == z && e->mc == g->mc &&
        e->dim == g->dim && e->flags == g->flags)
        return e->b - 1;
    int b = getBiomeAt(g, 4, x, 0, z);
    e->seed = g->seed; e->x = x; e->z = z; e->mc = g->mc; e->dim = g->dim; e->flags = g->flags;
    e->b = b + 1;                       /* 0 = empty slot (b may be -1: none) */
    return b;
}

/* the 1:1 biome: 1.16.1's overworld uses HorizontalVoronoiBiomeAccessType
   (the 3-D voronoi at y = 0 — not cubiomes' scale-1 layer) */
static int t116_biome1(const T116 *t, const T116Chunk *c, int x, int z)
{
    int ax, ay, az;
    voronoiAccess3D(t->g.sha, x, 0, z, &ax, &ay, &az);
    int i = ax - (c->cx * 4 - 2), j = az - (c->cz * 4 - 2);
    if (i >= 0 && i < 9 && j >= 0 && j < 9) return c->bio4[j * 9 + i];
    return t116_biome4(&t->g, ax, az);
}

static void sb_default(T116Chunk *c, uint64_t *rnd, const T116Biome *bi, int x, int z, int height,
                       double noise, int topBlock, int underBlock, int underwaterBlock)
{
    const int seaLevel = 63;
    int blockState = topBlock, blockState2 = underBlock;
    int i = -1;
    int j = (int)(noise / 3.0 + 3.0 + nextDouble(rnd) * 0.25);
    int k = x & 0xF, l = z & 0xF;
    for (int m = height; m >= 0; --m) {
        int b3 = B(k, m, l);
        if (T116_IS_AIR(b3)) { i = -1; continue; }
        if (b3 != T116_STONE) continue;
        if (i == -1) {
            if (j <= 0) {
                blockState = T116_AIR;
                blockState2 = T116_STONE;
            } else if (m >= seaLevel - 4 && m <= seaLevel + 1) {
                blockState = topBlock;
                blockState2 = underBlock;
            }
            if (m < seaLevel && T116_IS_AIR(blockState))
                blockState = biome_temp(bi, x, z) < 0.15f ? T116_ICE : T116_WATER;
            i = j;
            if (m >= seaLevel - 1) { B(k, m, l) = (uint8_t)blockState; continue; }
            if (m < seaLevel - 7 - j) {
                blockState = T116_AIR;
                blockState2 = T116_STONE;
                B(k, m, l) = (uint8_t)underwaterBlock;
                continue;
            }
            B(k, m, l) = (uint8_t)blockState2;
            continue;
        }
        if (i <= 0) continue;
        B(k, m, l) = (uint8_t)blockState2;
        if (--i != 0 || (blockState2 != T116_SAND && blockState2 != T116_RED_SAND) || j <= 1) continue;
        /* (isOf(SAND): red sand is another block — kept as the Java test) */
        if (blockState2 != T116_SAND) continue;
        i = nextInt(rnd, 4) + (m - 63 > 0 ? m - 63 : 0);
        blockState2 = T116_SANDSTONE;
    }
}

static int badlands_layer(const T116 *t, int x, int y, int z)
{
    double v = octsimplex(&t->bl_layer, (double)x / 512.0, (double)z / 512.0, 0) * 2.0;
    int i = (int)(long long)floor(v + 0.5);
    return t->bl_layers[(y + i + 64) % 64];
}

/* kind 6 badlands, 7 wooded badlands, 8 eroded badlands */
static void sb_badlands(const T116 *t, T116Chunk *c, uint64_t *rnd, const T116Biome *bi, int kind,
                        int i, int j, int k, double d)
{
    const int l = 63;
    int n = i & 0xF, o = j & 0xF;
    double e = 0.0;
    if (kind == 8) {
        double f = fabs(d);
        double hc = octsimplex(&t->bl_cut, (double)i * 0.25, (double)j * 0.25, 0) * 15.0;
        if (hc < f) f = hc;
        if (f > 0.0) {
            e = f * f * 2.5;
            double h = fabs(octsimplex(&t->bl_height, (double)i * 0.001953125, (double)j * 0.001953125, 0));
            double nn = ceil(h * 50.0) + 14.0;
            if (e > nn) e = nn;
            e += 64.0;
        }
    }
    int bs3 = T116_WHITE_T;
    int bs4 = bi->under;
    int p = (int)(d / 3.0 + 3.0 + nextDouble(rnd) * 0.25);
    int bl = cos(d / 3.0 * 3.141592653589793) > 0.0;
    int q = -1, bl2 = 0, r = 0;
    int s0 = k;
    if (kind == 8 && (int)e + 1 > s0) s0 = (int)e + 1;
    for (int s = s0; s >= 0; --s) {
        if (kind != 8 && r >= 15) continue;
        if (kind == 8 && T116_IS_AIR(B(n, s, o)) && s < (int)e)
            B(n, s, o) = T116_STONE;
        int b5 = B(n, s, o);
        if (T116_IS_AIR(b5)) { q = -1; continue; }
        if (b5 != T116_STONE) continue;
        if (q == -1) {
            bl2 = 0;
            if (p <= 0) {
                bs3 = T116_AIR;
                bs4 = T116_STONE;
            } else if (s >= l - 4 && s <= l + 1) {
                bs3 = T116_WHITE_T;
                bs4 = bi->under;
            }
            if (s < l && T116_IS_AIR(bs3)) bs3 = T116_WATER;
            q = p + (s - l > 0 ? s - l : 0);
            if (s >= l - 1) {
                if (kind == 7 && s > 86 + p * 2) {
                    B(n, s, o) = (uint8_t)(bl ? T116_COARSE_DIRT : T116_GRASS);
                } else if (s > l + 3 + p) {
                    int b8;
                    if (s < 64 || s > 127) b8 = T116_ORANGE_T;
                    else if (bl) b8 = T116_TERRACOTTA;
                    else b8 = badlands_layer(t, i, s, j);
                    B(n, s, o) = (uint8_t)b8;
                } else {
                    B(n, s, o) = (uint8_t)bi->top;
                    bl2 = 1;
                }
            } else {
                B(n, s, o) = (uint8_t)bs4;
                if (kind == 7) {
                    if (bs4 == T116_WHITE_T) B(n, s, o) = T116_ORANGE_T;
                } else if (is_colored_t(bs4) || bs4 == T116_WHITE_T) {
                    B(n, s, o) = T116_ORANGE_T;
                }
            }
        } else if (q > 0) {
            --q;
            B(n, s, o) = (uint8_t)(bl2 ? T116_ORANGE_T : badlands_layer(t, i, s, j));
        }
        ++r;
    }
}

static void sb_frozen_ocean(const T116 *t, T116Chunk *c, uint64_t *rnd, const T116Biome *bi,
                            int i, int j, int k, double d)
{
    const int l = 63;
    double e = 0.0, f = 0.0;
    float g = biome_temp(bi, i, j);
    double h = fabs(d);
    double hh = octsimplex(&t->fo_a, (double)i * 0.1, (double)j * 0.1, 0) * 15.0;
    if (hh < h) h = hh;
    if (h > 1.8) {
        e = h * h * 1.2;
        double o = fabs(octsimplex(&t->fo_b, (double)i * 0.09765625, (double)j * 0.09765625, 0));
        double p = ceil(o * 40.0) + 14.0;
        if (e > p) e = p;
        if (g > 0.1f) e -= 2.0;
        if (e > 2.0) {
            f = (double)l - e - 7.0;
            e += (double)l;
        } else {
            e = 0.0;
        }
    }
    int q = i & 0xF, r = j & 0xF;
    int bs3 = bi->under, bs4 = bi->top;
    int s = (int)(d / 3.0 + 3.0 + nextDouble(rnd) * 0.25);
    int tt = -1, u = 0;
    int v = 2 + nextInt(rnd, 4);
    int w = l + 18 + nextInt(rnd, 10);
    int x0 = k > (int)e + 1 ? k : (int)e + 1;
    for (int x = x0; x >= 0; --x) {
        if (T116_IS_AIR(B(q, x, r)) && x < (int)e && nextDouble(rnd) > 0.01) {
            B(q, x, r) = T116_PACKED_ICE;
        } else if (B(q, x, r) == T116_WATER && x > (int)f && x < l && f != 0.0 && nextDouble(rnd) > 0.15) {
            B(q, x, r) = T116_PACKED_ICE;
        }
        int b5 = B(q, x, r);
        if (T116_IS_AIR(b5)) { tt = -1; continue; }
        if (b5 == T116_STONE) {
            if (tt == -1) {
                if (s <= 0) {
                    bs4 = T116_AIR;
                    bs3 = T116_STONE;
                } else if (x >= l - 4 && x <= l + 1) {
                    bs4 = bi->top;
                    bs3 = bi->under;
                }
                if (x < l && T116_IS_AIR(bs4))
                    bs4 = biome_temp(bi, i, j) < 0.15f ? T116_ICE : T116_WATER;
                tt = s;
                if (x >= l - 1) { B(q, x, r) = (uint8_t)bs4; continue; }
                if (x < l - 7 - s) {
                    bs4 = T116_AIR;
                    bs3 = T116_STONE;
                    B(q, x, r) = T116_GRAVEL;
                    continue;
                }
                B(q, x, r) = (uint8_t)bs3;
                continue;
            }
            if (tt <= 0) continue;
            B(q, x, r) = (uint8_t)bs3;
            if (--tt != 0 || bs3 != T116_SAND || s <= 1) continue;
            tt = nextInt(rnd, 4) + (x - 63 > 0 ? x - 63 : 0);
            bs3 = T116_SANDSTONE;
            continue;
        }
        if (b5 != T116_PACKED_ICE || u > v || x <= w) continue;
        B(q, x, r) = T116_SNOW_BLOCK;
        ++u;
    }
}

static void t116_surface(const T116 *t, T116Chunk *c)
{
    uint64_t rnd;
    int cx = c->cx, cz = c->cz;
    setSeed(&rnd, (uint64_t)((int64_t)cx * 341873128712LL + (int64_t)cz * 132897987541LL));
    int k = cx * 16, l = cz * 16;
    /* (10/10) the highest layer holding a non-air block (T116_AIR = 0): no
     * column goes higher, and a column is only changed at its own turn */
    int ymax = 255;
    while (ymax >= 0) {
        uint64_t w[32], any = 0;
        memcpy(w, &c->b[ymax * 256], sizeof w);
        for (int i = 0; i < 32; ++i) any |= w[i];
        if (any) break;
        --ymax;
    }
    for (int m = 0; m < 16; ++m)
        for (int n = 0; n < 16; ++n) {
            int o = k + m, p = l + n;
            int top = -1;                    /* WORLD_SURFACE_WG: highest non-air */
            for (int y = ymax; y >= 0; --y)
                if (!T116_IS_AIR(B(m, y, n))) { top = y; break; }
            int q = top + 1;              /* sampleHeightmap (= top block) + 1 */
            double e = octsimplex(&t->surf, (double)o * 0.0625, (double)p * 0.0625, 1) * 0.55 * 15.0;
            int id = t116_biome1(t, c, o, p);
            const T116Biome *bi = tb(id);
            int height = q;
            switch (bi->sb) {
            case 1:
                sb_default(c, &rnd, bi, o, p, height, e, e > 1.0 ? T116_STONE : T116_GRASS,
                           e > 1.0 ? T116_STONE : T116_DIRT, T116_GRAVEL);
                break;
            case 2:
                if (e < -1.0 || e > 2.0) sb_default(c, &rnd, bi, o, p, height, e, T116_GRAVEL, T116_GRAVEL, T116_GRAVEL);
                else if (e > 1.0) sb_default(c, &rnd, bi, o, p, height, e, T116_STONE, T116_STONE, T116_GRAVEL);
                else sb_default(c, &rnd, bi, o, p, height, e, T116_GRASS, T116_DIRT, T116_GRAVEL);
                break;
            case 3:
                if (e > 1.75) sb_default(c, &rnd, bi, o, p, height, e, T116_STONE, T116_STONE, T116_GRAVEL);
                else if (e > -0.5) sb_default(c, &rnd, bi, o, p, height, e, T116_COARSE_DIRT, T116_DIRT, T116_GRAVEL);
                else sb_default(c, &rnd, bi, o, p, height, e, T116_GRASS, T116_DIRT, T116_GRAVEL);
                break;
            case 4:
                if (e > 1.75) sb_default(c, &rnd, bi, o, p, height, e, T116_COARSE_DIRT, T116_DIRT, T116_GRAVEL);
                else if (e > -0.95) sb_default(c, &rnd, bi, o, p, height, e, T116_PODZOL, T116_DIRT, T116_GRAVEL);
                else sb_default(c, &rnd, bi, o, p, height, e, T116_GRASS, T116_DIRT, T116_GRAVEL);
                break;
            case 5: {
                double fe = octsimplex(&FOLIAGE, (double)o * 0.25, (double)p * 0.25, 0);
                if (fe > 0.0) {
                    for (int y = height; y >= 0; --y) {
                        if (T116_IS_AIR(B(m, y, n))) continue;
                        if (y != 62 || B(m, y, n) == T116_WATER) break;
                        B(m, y, n) = T116_WATER;
                        break;
                    }
                }
                sb_default(c, &rnd, bi, o, p, height, e, bi->top, bi->under, bi->underwater);
                break;
            }
            case 6: case 7: case 8:
                sb_badlands(t, c, &rnd, bi, bi->sb, o, p, height, e);
                break;
            case 9:
                sb_frozen_ocean(t, c, &rnd, bi, o, p, height, e);
                break;
            default:
                sb_default(c, &rnd, bi, o, p, height, e, bi->top, bi->under, bi->underwater);
            }
        }
    /* bedrock floor (y 0..4), BlockPos.iterate: x fastest */
    for (int z = 0; z < 16; ++z)
        for (int x = 0; x < 16; ++x)
            for (int o = 4; o >= 0; --o) {
                if (o > nextInt(&rnd, 5)) continue;
                B(x, o, z) = T116_BEDROCK;
            }
}

/* ══ carvers (Carver / CaveCarver / RavineCarver / Underwater*) ══════════════ */
static float SINT[65536];
static int SINT_OK = 0;
static inline int f2i(float f)           /* Java (int) of a float */
{
    if (f != f) return 0;
    if (f >= 2147483647.0f) return 2147483647;
    if (f <= -2147483648.0f) return (int)0x80000000;
    return (int)f;
}
static inline float msin(float f) { return SINT[f2i(f * 10430.378f) & 0xFFFF]; }
static inline float mcos(float f) { return SINT[f2i(f * 10430.378f + 16384.0f) & 0xFFFF]; }
static void sint_init(void)
{
    if (SINT_OK) return;
    for (int i = 0; i < 65536; ++i) SINT[i] = (float)sin((double)i * 3.141592653589793 * 2.0 / 65536.0);
    SINT_OK = 1;
}

enum { CV_CAVE = 0, CV_RAVINE = 1, CV_UW_CAVE = 2, CV_UW_RAVINE = 3 };

typedef struct {
    const T116 *t;
    T116Chunk *c;
    int kind;
    uint8_t *mask;
    uint64_t *rr, rrv;                  /* carveRegion's Random(seed + chunkX + chunkZ) */
    uint8_t *sup;                       /* superset mode: every cell the tunnels reach (all stone) */
    float stretch[256];
    struct CarvRec_s *rec;              /* (10/10) record mode: the regions, not the blocks */
} Carv;

/* (10/10) the carveRegion calls reaching a chunk (seed, centre, radii, kind,
 * ravine stretch) only depend on the 48 low bits and on the chunk being ocean
 * or not: recorded once per structure seed, then replayed on each sister seed
 * in the same order (the blocks they carve depend on its terrain). */
typedef struct { uint64_t seed; double x, y, z, yaw, pitch; int kind, st; } CarvReg;
typedef struct CarvRec_s {
    uint64_t low; int mc, cx, cz, liquid, ocean, ok;
    int n, cap; CarvReg *r;
    int nst, capst; float (*st)[256];
    int cur_st;                         /* while recording: stretch index of the ravine */
} CarvRec;

static int carv_rec_push(CarvRec *R, uint64_t seed, double x, double y, double z, double yaw,
                         double pitch, int kind)
{
    if (R->n == R->cap) {
        int nc = R->cap ? R->cap * 2 : 64;
        CarvReg *t = (CarvReg *)realloc(R->r, sizeof(CarvReg) * (size_t)nc);
        if (!t) return 0;
        R->r = t; R->cap = nc;
    }
    R->r[R->n++] = (CarvReg){ seed, x, y, z, yaw, pitch, kind, R->cur_st };
    return 1;
}

static inline int carvable_air(int b)
{
    return b == T116_STONE || b == T116_DIRT || b == T116_COARSE_DIRT || b == T116_PODZOL ||
           b == T116_GRASS || is_terracotta(b) || b == T116_SANDSTONE || b == T116_RED_SANDSTONE ||
           b == T116_MYCELIUM || b == T116_PACKED_ICE;
}
static inline int carvable_uw(int b, int kind)
{
    if (carvable_air(b) && (b != T116_PACKED_ICE || kind == CV_UW_CAVE)) return 1;
    return b == T116_SAND || b == T116_GRAVEL || b == T116_WATER || b == T116_LAVA ||
           b == T116_OBSIDIAN || T116_IS_AIR(b);
}

static int region_uncarvable(T116Chunk *c, int i0, int i1, int miny, int maxy, int j0, int j1)
{
    for (int i = i0; i < i1; ++i)
        for (int j = j0; j < j1; ++j)
            for (int k = miny - 1; k <= maxy + 1; ++k) {
                if (k >= 0 && k < 256 && T116_AT(c, i, k, j) == T116_WATER) return 1;
                if (k == maxy + 1 || i == i0 || i == i1 - 1 || j == j0 || j == j1 - 1) continue;
                k = maxy;
            }
    return 0;
}

static void carve_point(Carv *cv, int x, int z, int rx, int y, int rz, int *grass)
{
    T116Chunk *c = cv->c;
    if (cv->sup) {                      /* superset: the air it could make (cave air from y 11) */
        if (y >= 11) { int i = rx | rz << 4 | y << 8; cv->sup[i >> 3] |= (uint8_t)(1 << (i & 7)); }
        return;
    }
    if (cv->kind >= CV_UW_CAVE) {
        if (y >= 63) return;
        int i = rx | rz << 4 | y << 8;
        if (cv->mask[i >> 3] & (1 << (i & 7))) return;
        cv->mask[i >> 3] |= (uint8_t)(1 << (i & 7));
        int b = T116_AT(c, rx, y, rz);
        if (!carvable_uw(b, cv->kind)) return;
        if (y == 10) {
            float f = nextFloat(cv->rr);
            T116_AT(c, rx, y, rz) = (double)f < 0.25 ? T116_MAGMA : T116_OBSIDIAN;
            return;
        }
        if (y < 10) { T116_AT(c, rx, y, rz) = T116_LAVA; return; }
        /* the Java loop moves `pos` to a neighbour only when that neighbour is
           in the chunk (short-circuit): the water lands on the first in-chunk
           air neighbour (N E S W), or — at a neighbour outside the chunk — on
           the last in-chunk neighbour looked at (a solid one) / the block */
        static const int DX[4] = {0, 1, 0, -1}, DZ[4] = {-1, 0, 1, 0};
        int px = x, pz = z;
        for (int d = 0; d < 4; ++d) {
            int j = x + DX[d], k = z + DZ[d];
            if ((j >> 4) == c->cx && (k >> 4) == c->cz) {
                px = j; pz = k;
                if (!T116_IS_AIR(T116_AT(c, j & 15, y, k & 15))) continue;
            }
            T116_AT(c, px & 15, y, pz & 15) = T116_WATER;
            return;
        }
        T116_AT(c, rx, y, rz) = T116_WATER;
        return;
    }
    int i = rx | rz << 4 | y << 8;
    if (cv->mask[i >> 3] & (1 << (i & 7))) return;
    cv->mask[i >> 3] |= (uint8_t)(1 << (i & 7));
    int b = T116_AT(c, rx, y, rz);
    int above = y < 255 ? T116_AT(c, rx, y + 1, rz) : T116_AIR;
    if (b == T116_GRASS || b == T116_MYCELIUM) *grass = 1;
    if (!(carvable_air(b) || ((b == T116_SAND || b == T116_GRAVEL) && above != T116_WATER))) return;
    if (y < 11) {
        T116_AT(c, rx, y, rz) = T116_LAVA;
    } else {
        T116_AT(c, rx, y, rz) = T116_CAVE_AIR;
        if (*grass && y > 0 && T116_AT(c, rx, y - 1, rz) == T116_DIRT) {
            int id = t116_biome1(cv->t, c, x, z);
            T116_AT(c, rx, y - 1, rz) = (uint8_t)tb(id)->top;
        }
    }
}

static void carve_region(Carv *cv, uint64_t seed, double x, double y, double z, double yaw, double pitch)
{
    int chunkX = cv->c->cx, chunkZ = cv->c->cz;
    setSeed(&cv->rrv, seed + (uint64_t)(int64_t)chunkX + (uint64_t)(int64_t)chunkZ);
    cv->rr = &cv->rrv;
    double d = chunkX * 16 + 8, e = chunkZ * 16 + 8;
    if (x < d - 16.0 - yaw * 2.0 || z < e - 16.0 - yaw * 2.0 || x > d + 16.0 + yaw * 2.0 || z > e + 16.0 + yaw * 2.0)
        return;
    if (cv->rec) {
        if (!carv_rec_push(cv->rec, seed, x, y, z, yaw, pitch, cv->kind)) cv->rec->ok = -1;
        return;
    }
    int i = j_floor(x - yaw) - chunkX * 16 - 1; if (i < 0) i = 0;
    int j = j_floor(x + yaw) - chunkX * 16 + 1; if (j > 16) j = 16;
    int k = j_floor(y - pitch) - 1; if (k < 1) k = 1;
    int l = j_floor(y + pitch) + 1; if (l > 248) l = 248;
    int m = j_floor(z - yaw) - chunkZ * 16 - 1; if (m < 0) m = 0;
    int n = j_floor(z + yaw) - chunkZ * 16 + 1; if (n > 16) n = 16;
    if (cv->kind < CV_UW_CAVE && !cv->sup && region_uncarvable(cv->c, i, j, k, l, m, n)) return;
    for (int o = i; o < j; ++o) {
        int p = o + chunkX * 16;
        double f = ((double)p + 0.5 - x) / yaw;
        for (int q = m; q < n; ++q) {
            int r = q + chunkZ * 16;
            double g = ((double)r + 0.5 - z) / yaw;
            if (f * f + g * g >= 1.0) continue;
            int grass = 0;
            for (int s = l; s > k; --s) {
                double h = ((double)s - 0.5 - y) / pitch;
                if (cv->kind == CV_CAVE || cv->kind == CV_UW_CAVE) {
                    if (h <= -0.7 || f * f + h * h + g * g >= 1.0) continue;
                } else {
                    if ((f * f + g * g) * (double)cv->stretch[s - 1] + h * h / 6.0 >= 1.0) continue;
                }
                carve_point(cv, p, r, o, s, q, &grass);
            }
        }
    }
}

static int can_carve_branch(int mcx, int mcz, double x, double z, int branch, int count, float width)
{
    double d = mcx * 16 + 8;
    double f = x - d;
    double e = mcz * 16 + 8;
    double g = z - e;
    double h = count - branch;
    double i = (double)(width + 2.0f + 16.0f);
    return f * f + g * g - h * h <= i * i;
}

static void carve_tunnels(Carv *cv, uint64_t seed, double x, double y, double z, float width, float yaw,
                          float pitch, int start, int count, double ratio)
{
    uint64_t R;
    setSeed(&R, seed);
    int i = nextInt(&R, count / 2) + count / 4;
    int bl = nextInt(&R, 6) == 0;
    float f = 0.0f, g = 0.0f;
    for (int j = start; j < count; ++j) {
        double d = 1.5 + (double)(msin((float)3.141592653589793 * (float)j / (float)count) * width);
        double e = d * ratio;
        float h = mcos(pitch);
        x += (double)(mcos(yaw) * h);
        y += (double)msin(pitch);
        z += (double)(msin(yaw) * h);
        pitch *= bl ? 0.92f : 0.7f;
        pitch += g * 0.1f;
        yaw += f * 0.1f;
        g *= 0.9f;
        f *= 0.75f;
        float a1 = nextFloat(&R), a2 = nextFloat(&R), a3 = nextFloat(&R);
        g += (a1 - a2) * a3 * 2.0f;
        a1 = nextFloat(&R); a2 = nextFloat(&R); a3 = nextFloat(&R);
        f += (a1 - a2) * a3 * 4.0f;
        if (j == i && width > 1.0f) {
            uint64_t s1 = nextLong(&R);
            float w1 = nextFloat(&R) * 0.5f + 0.5f;
            carve_tunnels(cv, s1, x, y, z, w1, yaw - 1.5707964f, pitch / 3.0f, j, count, 1.0);
            uint64_t s2 = nextLong(&R);
            float w2 = nextFloat(&R) * 0.5f + 0.5f;
            carve_tunnels(cv, s2, x, y, z, w2, yaw + 1.5707964f, pitch / 3.0f, j, count, 1.0);
            return;
        }
        if (nextInt(&R, 4) == 0) continue;
        if (!can_carve_branch(cv->c->cx, cv->c->cz, x, z, j, count, width)) return;
        carve_region(cv, seed, x, y, z, d, e);
    }
}

static void carve_cave_src(Carv *cv, uint64_t *r, int sx, int sz)
{
    const int n = 112;
    int o = nextInt(r, nextInt(r, nextInt(r, 15) + 1) + 1);
    for (int p = 0; p < o; ++p) {
        double d = sx * 16 + nextInt(r, 16);
        double e = nextInt(r, nextInt(r, 120) + 8);
        double f = sz * 16 + nextInt(r, 16);
        int q = 1;
        if (nextInt(r, 4) == 0) {
            float h = 1.0f + nextFloat(r) * 6.0f;
            uint64_t s = nextLong(r);
            double dd = 1.5 + (double)(msin(1.5707964f) * h);
            carve_region(cv, s, d + 1.0, e, f, dd, dd * 0.5);
            q += nextInt(r, 4);
        }
        for (int k = 0; k < q; ++k) {
            float s = nextFloat(r) * ((float)3.141592653589793 * 2.0f);
            float t = (nextFloat(r) - 0.5f) / 4.0f;
            float u = nextFloat(r) * 2.0f;
            u += nextFloat(r);
            if (nextInt(r, 10) == 0) {
                float a = nextFloat(r);
                float b = nextFloat(r);
                u *= a * b * 3.0f + 1.0f;
            }
            int v = n - nextInt(r, n / 4);
            uint64_t sd = nextLong(r);
            carve_tunnels(cv, sd, d, e, f, u, s, t, 0, v, 1.0);
        }
    }
}

static void carve_ravine_src(Carv *cv, uint64_t *r, int sx, int sz)
{
    const int n = 112;
    double d = sx * 16 + nextInt(r, 16);
    double e = nextInt(r, nextInt(r, 40) + 8) + 20;
    double f = sz * 16 + nextInt(r, 16);
    float g = nextFloat(r) * ((float)3.141592653589793 * 2.0f);
    float h = (nextFloat(r) - 0.5f) * 2.0f / 8.0f;
    float p = nextFloat(r) * 2.0f;
    p += nextFloat(r);
    p *= 2.0f;
    int q = n - nextInt(r, n / 4);
    uint64_t seed = nextLong(r);
    /* carveRavine */
    uint64_t R;
    setSeed(&R, seed);
    float ff = 1.0f;
    for (int i = 0; i < 256; ++i) {
        if (i == 0 || nextInt(&R, 3) == 0) {
            float a = nextFloat(&R);
            ff = 1.0f + a * nextFloat(&R);
        }
        cv->stretch[i] = ff * ff;
    }
    if (cv->rec) {                      /* keep this ravine's stretch for the replay */
        CarvRec *R = cv->rec;
        if (R->nst == R->capst) {
            int nc = R->capst ? R->capst * 2 : 4;
            float (*t)[256] = realloc(R->st, sizeof(float[256]) * (size_t)nc);
            if (!t) R->ok = -1; else { R->st = t; R->capst = nc; }
        }
        if (R->nst < R->capst) { memcpy(R->st[R->nst], cv->stretch, sizeof(cv->stretch)); R->cur_st = R->nst++; }
    }
    float yaw = g, pitch = h, gg = 0.0f, hh = 0.0f;
    double x = d, y = e, z = f;
    for (int j = 0; j < q; ++j) {
        double dd = 1.5 + (double)(msin((float)j * (float)3.141592653589793 / (float)q) * p);
        double ee = dd * 3.0;
        dd *= (double)nextFloat(&R) * 0.25 + 0.75;
        ee *= (double)nextFloat(&R) * 0.25 + 0.75;
        float k = mcos(pitch), l = msin(pitch);
        x += (double)(mcos(yaw) * k);
        y += (double)l;
        z += (double)(msin(yaw) * k);
        pitch *= 0.7f;
        pitch += hh * 0.05f;
        yaw += gg * 0.05f;
        hh *= 0.8f;
        gg *= 0.5f;
        float a1 = nextFloat(&R), a2 = nextFloat(&R), a3 = nextFloat(&R);
        hh += (a1 - a2) * a3 * 2.0f;
        a1 = nextFloat(&R); a2 = nextFloat(&R); a3 = nextFloat(&R);
        gg += (a1 - a2) * a3 * 4.0f;
        if (nextInt(&R, 4) == 0) continue;
        if (!can_carve_branch(cv->c->cx, cv->c->cz, x, z, j, q, p)) return;
        carve_region(cv, seed, x, y, z, dd, ee);
    }
}

static __thread CarvRec *CARV_C = NULL;     /* [64] */

static void t116_carve(const T116 *t, T116Chunk *c, int liquid)
{
    sint_init();
    const T116Biome *bi = tb(c->bio4[2 * 9 + 2]);
    int ocean = (bi->flags & 2) != 0;
    if (liquid && !ocean) return;
    Carv cv;
    cv.t = t;
    cv.c = c;
    cv.sup = NULL;
    cv.rec = NULL;
    cv.mask = liquid ? c->mask_liq : c->mask_air;
    float pcave = ocean ? 0.06666667f : 0.14285715f;
    uint64_t low = t->seed & 0xFFFFFFFFFFFFULL;
    if (!CARV_C) CARV_C = (CarvRec *)calloc(64, sizeof(CarvRec));
    CarvRec *R = CARV_C ? &CARV_C[((unsigned)c->cx * 31u + (unsigned)c->cz * 17u +
                                   (unsigned)liquid * 5u + (unsigned)ocean * 11u) & 63] : NULL;
    if (R && !(R->ok == 1 && R->low == low && R->mc == t->mc && R->cx == c->cx && R->cz == c->cz &&
               R->liquid == liquid && R->ocean == ocean)) {
        /* record the regions of this chunk (no block touched) */
        R->ok = 0; R->n = 0; R->nst = 0; R->cur_st = -1;
        cv.rec = R;
        for (int l = c->cx - 8; l <= c->cx + 8; ++l)
            for (int m = c->cz - 8; m <= c->cz + 8; ++m)
                for (int n = 0; n < 2; ++n) {
                    uint64_t r;
                    uint64_t ws = t->seed + (uint64_t)n;
                    setSeed(&r, ws);
                    uint64_t a = nextLong(&r), b = nextLong(&r);
                    setSeed(&r, (uint64_t)((int64_t)l * (int64_t)a) ^ (uint64_t)((int64_t)m * (int64_t)b) ^ ws);
                    int kind = liquid ? (n == 0 ? CV_UW_RAVINE : CV_UW_CAVE) : (n == 0 ? CV_CAVE : CV_RAVINE);
                    float pr = (kind == CV_CAVE || kind == CV_UW_CAVE) ? pcave : 0.02f;
                    if (!(nextFloat(&r) <= pr)) continue;
                    cv.kind = kind;
                    R->cur_st = -1;
                    if (kind == CV_CAVE || kind == CV_UW_CAVE) carve_cave_src(&cv, &r, l, m);
                    else carve_ravine_src(&cv, &r, l, m);
                }
        cv.rec = NULL;
        if (R->ok == 0) {
            R->low = low; R->mc = t->mc; R->cx = c->cx; R->cz = c->cz;
            R->liquid = liquid; R->ocean = ocean; R->ok = 1;
        } else R->ok = 0;               /* out of memory: carve directly below */
    }
    if (R && R->ok == 1) {
        for (int i = 0; i < R->n; i++) {
            const CarvReg *g = &R->r[i];
            cv.kind = g->kind;
            if (g->st >= 0) memcpy(cv.stretch, R->st[g->st], sizeof(cv.stretch));
            carve_region(&cv, g->seed, g->x, g->y, g->z, g->yaw, g->pitch);
        }
        return;
    }
    for (int l = c->cx - 8; l <= c->cx + 8; ++l)
        for (int m = c->cz - 8; m <= c->cz + 8; ++m)
            for (int n = 0; n < 2; ++n) {
                uint64_t r;
                uint64_t ws = t->seed + (uint64_t)n;
                setSeed(&r, ws);
                uint64_t a = nextLong(&r), b = nextLong(&r);
                setSeed(&r, (uint64_t)((int64_t)l * (int64_t)a) ^ (uint64_t)((int64_t)m * (int64_t)b) ^ ws);
                int kind = liquid ? (n == 0 ? CV_UW_RAVINE : CV_UW_CAVE) : (n == 0 ? CV_CAVE : CV_RAVINE);
                float pr = (kind == CV_CAVE || kind == CV_UW_CAVE) ? pcave : 0.02f;
                if (!(nextFloat(&r) <= pr)) continue;
                cv.kind = kind;
                if (kind == CV_CAVE || kind == CV_UW_CAVE) carve_cave_src(&cv, &r, l, m);
                else carve_ravine_src(&cv, &r, l, m);
            }
}

int t116_chunk_build(const T116 *t, int cx, int cz, int status, T116Chunk *c)
{
    c->cx = cx;
    c->cz = cz;
    Range r = {4, cx * 4 - 2, cz * 4 - 2, 9, 9, 0, 1};
    int *bc = allocCache(&t->g, r);
    if (!bc) return 0;
    genBiomes(&t->g, bc, r);
    memcpy(c->bio4, bc, sizeof(c->bio4));
    free(bc);
    t116_noise(t, cx, cz, c->bio4, c->b);
    if (status >= 5) t116_surface(t, c);
    if (status >= 6) { memset(c->mask_air, 0, sizeof(c->mask_air)); t116_carve(t, c, 0); }
    if (status >= 7) { memset(c->mask_liq, 0, sizeof(c->mask_liq)); t116_carve(t, c, 1); }
    return 1;
}

int t116_chunk_noise(const T116 *t, int cx, int cz, uint8_t *out)
{
    static __thread T116Chunk c;
    if (!t116_chunk_build(t, cx, cz, 4, &c)) return 0;
    memcpy(out, c.b, 65536);
    return 1;
}


/* ══ features: lakes and dungeons of one chunk ══════════════════════════════ */
struct T116Cache {
    int cap, n;
    uint64_t tick;
    struct { int cx, cz, st; uint64_t used; T116Chunk *c; } *e;
    uint64_t seed;
    int mc;
};

T116Cache *t116_cache_new(int cap)
{
    T116Cache *c = calloc(1, sizeof(*c));
    if (!c) return NULL;
    c->cap = cap < 9 ? 9 : cap;
    c->e = calloc((size_t)c->cap, sizeof(*c->e));
    return c;
}

void t116_cache_free(T116Cache *c)
{
    if (!c) return;
    for (int i = 0; i < c->n; ++i) free(c->e[i].c);
    free(c->e);
    free(c);
}

/* the chunk at a status (4..7, built once, kept) */
const T116Chunk *t116_cache_chunk(const T116 *t, T116Cache *c, int cx, int cz, int st)
{
    if (c->seed != t->seed || c->mc != t->mc) {
        for (int i = 0; i < c->n; ++i) free(c->e[i].c);
        c->n = 0;
        c->seed = t->seed;
        c->mc = t->mc;
    }
    c->tick++;
    for (int i = 0; i < c->n; ++i)
        if (c->e[i].cx == cx && c->e[i].cz == cz && c->e[i].st == st) { c->e[i].used = c->tick; return c->e[i].c; }
    int k;
    if (c->n < c->cap) {
        k = c->n++;
        c->e[k].c = malloc(sizeof(T116Chunk));
        if (!c->e[k].c) { c->n--; return NULL; }
    } else {
        k = 0;
        for (int i = 1; i < c->n; ++i) if (c->e[i].used < c->e[k].used) k = i;
    }
    c->e[k].cx = cx; c->e[k].cz = cz; c->e[k].st = st; c->e[k].used = c->tick;
    t116_chunk_build(t, cx, cz, st, c->e[k].c);
    return c->e[k].c;
}

static const T116Chunk *cache_get(const T116 *t, T116Cache *c, int cx, int cz)
{
    return t116_cache_chunk(t, c, cx, cz, 7);
}

/* the highest solid block of a column (-1: none) */
int t116_top_solid(const T116Chunk *c, int x, int z)
{
    for (int y = 255; y >= 0; --y)
        if (T116_IS_SOLID(T116_AT(c, x & 15, y, z & 15))) return y;
    return -1;
}

/* the 3×3 chunks around a populated one, fetched lazily: a chunk is built
   (cache) and copied into the scratch only when a feature first touches it */
typedef struct {
    int cx, cz;
    T116Chunk *ch[9];
    T116Chunk *scratch;
    const T116 *t;
    T116Cache *cache;
} W116;

static T116Chunk *w_chunk(W116 *w, int k)
{
    if (!w->ch[k]) {
        const T116Chunk *c = cache_get(w->t, w->cache, w->cx - 1 + k % 3, w->cz - 1 + k / 3);
        if (!c) return NULL;
        memcpy(&w->scratch[k], c, sizeof(T116Chunk));
        w->ch[k] = &w->scratch[k];
    }
    return w->ch[k];
}

static inline uint8_t *wb(W116 *w, int x, int y, int z)
{
    if (y < 0 || y > 255) return NULL;
    int i = (x >> 4) - w->cx + 1, j = (z >> 4) - w->cz + 1;
    if (i < 0 || i > 2 || j < 0 || j > 2) return NULL;
    T116Chunk *c = w->ch[j * 3 + i];
    if (!c && !(c = w_chunk(w, j * 3 + i))) return NULL;
    return &T116_AT(c, x & 15, y, z & 15);
}
static inline int wget(W116 *w, int x, int y, int z)
{
    uint8_t *p = wb(w, x, y, z);
    return p ? *p : T116_AIR;                /* outside: void air */
}
static inline void wset(W116 *w, int x, int y, int z, int b)
{
    uint8_t *p = wb(w, x, y, z);
    if (p) *p = (uint8_t)b;
}

static uint64_t pop_seed(uint64_t ws, int bx, int bz)
{
    uint64_t r;
    setSeed(&r, ws);
    uint64_t a = nextLong(&r) | 1, b = nextLong(&r) | 1;
    return ((uint64_t)(int64_t)bx * a + (uint64_t)(int64_t)bz * b) ^ ws;
}

/* LakeFeature.generate (after the decorator): 1 placed */
static int lake_generate(W116 *w, uint64_t *r, int x, int y, int z, int liquid, T116Feat *f)
{
    while (y > 5 && T116_IS_AIR(wget(w, x, y, z))) --y;
    if (y <= 4) return 0;
    y -= 4;
    f->ox = x; f->oy = y; f->oz = z;
    /* (village check not modelled) */
    static __thread uint8_t bls[2048];
    memset(bls, 0, sizeof(bls));
    int i = nextInt(r, 4) + 4;
    for (int j = 0; j < i; ++j) {
        double d = nextDouble(r) * 6.0 + 3.0;
        double e = nextDouble(r) * 4.0 + 2.0;
        double ff = nextDouble(r) * 6.0 + 3.0;
        double g = nextDouble(r) * (16.0 - d - 2.0) + 1.0 + d / 2.0;
        double h = nextDouble(r) * (8.0 - e - 4.0) + 2.0 + e / 2.0;
        double k = nextDouble(r) * (16.0 - ff - 2.0) + 1.0 + ff / 2.0;
        for (int l = 1; l < 15; ++l)
            for (int m = 1; m < 15; ++m)
                for (int n = 1; n < 7; ++n) {
                    double o = ((double)l - g) / (d / 2.0);
                    double p = ((double)n - h) / (e / 2.0);
                    double q = ((double)m - k) / (ff / 2.0);
                    double rr = o * o + p * p + q * q;
                    if (rr < 1.0) bls[(l * 16 + m) * 8 + n] = 1;
                }
    }
#define BL(s, t, u) bls[((s) * 16 + (t)) * 8 + (u)]
#define EDGE(s, t, u) (!BL(s, t, u) && (((s) < 15 && BL((s) + 1, t, u)) || ((s) > 0 && BL((s) - 1, t, u)) || \
        ((t) < 15 && BL(s, (t) + 1, u)) || ((t) > 0 && BL(s, (t) - 1, u)) || ((u) < 7 && BL(s, t, (u) + 1)) || ((u) > 0 && BL(s, t, (u) - 1))))
    for (int s = 0; s < 16; ++s)
        for (int t = 0; t < 16; ++t)
            for (int u = 0; u < 8; ++u) {
                if (!EDGE(s, t, u)) continue;
                int b = wget(w, x + s, y + u, z + t);
                if (u >= 4 && T116_IS_LIQUID(b)) return 0;
                if (u >= 4 || T116_IS_SOLID(b) || b == liquid) continue;
                return 0;
            }
    for (int v = 0; v < 16; ++v)
        for (int ww = 0; ww < 16; ++ww)
            for (int xx = 0; xx < 8; ++xx)
                if (BL(v, ww, xx)) wset(w, x + v, y + xx, z + ww, xx >= 4 ? T116_CAVE_AIR : liquid);
    /* (dirt → grass under the open lake: solid either way) */
    if (liquid == T116_LAVA) {
        for (int a = 0; a < 16; ++a)
            for (int c = 0; c < 16; ++c)
                for (int d = 0; d < 8; ++d) {
                    if (!EDGE(a, c, d)) continue;
                    if (d >= 4 && nextInt(r, 2) == 0) continue;
                    if (!T116_IS_SOLID(wget(w, x + a, y + d, z + c))) continue;
                    wset(w, x + a, y + d, z + c, T116_STONE);
                }
    }
    /* water: ice on its top layer needs water there — the layer is air: none */
#undef EDGE
#undef BL
    return 1;
}

/* DungeonFeature.generate at (x, y, z), the random after the decorator draws */
static int dungeon_generate(W116 *w, uint64_t *r, int X, int Y, int Z, int *pj, int *po)
{
    int j = nextInt(r, 2) + 2;
    int k = -j - 1, l = j + 1;
    int o = nextInt(r, 2) + 2;
    int p = -o - 1, q = o + 1;
    *pj = j; *po = o;
    int cnt = 0;
    for (int s = k; s <= l; ++s)
        for (int t = -1; t <= 4; ++t)
            for (int u = p; u <= q; ++u) {
                int b = wget(w, X + s, Y + t, Z + u);
                int bl = T116_IS_SOLID(b);
                if (t == -1 && !bl) return 0;
                if (t == 4 && !bl) return 0;
                if ((s != k && s != l && u != p && u != q) || t != 0 || !T116_IS_AIR(b) ||
                    !T116_IS_AIR(wget(w, X + s, Y + t + 1, Z + u))) continue;
                ++cnt;
            }
    if (cnt < 1 || cnt > 5) return 0;
    for (int v = k; v <= l; ++v)
        for (int ww = 3; ww >= -1; --ww)
            for (int x = p; x <= q; ++x) {
                int px = X + v, py = Y + ww, pz = Z + x;
                int b = wget(w, px, py, pz);
                if (v == k || ww == -1 || x == p || v == l || ww == 4 || x == q) {
                    if (py >= 0 && !T116_IS_SOLID(wget(w, px, py - 1, pz))) { wset(w, px, py, pz, T116_CAVE_AIR); continue; }
                    if (!T116_IS_SOLID(b) || b == T116_CHEST) continue;
                    if (ww == -1 && nextInt(r, 4) != 0) { wset(w, px, py, pz, T116_MOSSY); continue; }
                    wset(w, px, py, pz, T116_COBBLE);
                    continue;
                }
                if (b == T116_CHEST || b == T116_SPAWNER) continue;
                wset(w, px, py, pz, T116_CAVE_AIR);
            }
    static const int DX[4] = {0, 1, 0, -1}, DZ[4] = {-1, 0, 1, 0};
    for (int yy = 0; yy < 2; ++yy)
        for (int zz = 0; zz < 3; ++zz) {
            int aa = X + nextInt(r, j * 2 + 1) - j;
            int ac = Z + nextInt(r, o * 2 + 1) - o;
            if (!T116_IS_AIR(wget(w, aa, Y, ac))) continue;
            int ad = 0;
            for (int d = 0; d < 4; ++d) if (T116_IS_SOLID(wget(w, aa + DX[d], Y, ac + DZ[d]))) ++ad;
            if (ad != 1) continue;
            wset(w, aa, Y, ac, T116_CHEST);
            nextLong(r);                    /* the loot table seed */
            break;
        }
    wset(w, X, Y, Z, T116_SPAWNER);
    nextInt(r, 4);                          /* the spawner's mob */
    return 1;
}

static inline int is_desert_id(int b) { return b == 2 || b == 17 || b == 130; }

static int w_get(void *ctx, int x, int y, int z) { return wget((W116 *)ctx, x, y, z); }
static void w_set(void *ctx, int x, int y, int z, int b) { wset((W116 *)ctx, x, y, z, b); }

int t116_populate(const T116 *t, T116Cache *cache, int cx, int cz, T116Feat *out, int cap, T116Chunk *result)
{
    return t116_populate_ex(t, cache, cx, cz, out, cap, result, 3);
}

/* want: 1 the lakes, 2 the dungeons (the lakes are run anyway: they come first) */
int t116_populate_ex(const T116 *t, T116Cache *cache, int cx, int cz, T116Feat *out, int cap,
                     T116Chunk *result, int want)
{
    static __thread T116Chunk *scratch = NULL;
    if (!scratch) { scratch = malloc(9 * sizeof(T116Chunk)); if (!scratch) return -1; }
    W116 w;
    memset(&w, 0, sizeof w);
    w.cx = cx; w.cz = cz; w.scratch = scratch; w.t = t; w.cache = cache;
    if (!w_chunk(&w, 4)) return -1;
    int n = 0;
    int bio = w.ch[4]->bio4[(2 + 2) * 9 + (2 + 2)];   /* the 1:4 cell (cx*4+2, cz*4+2) */
    uint64_t ps = pop_seed(t->seed, cx * 16, cz * 16);
    int desert = is_desert_id(bio);
    /* LAKES (step 1): water lake (index 0, not in deserts), lava lake */
    for (int idx = 0; idx < 2; ++idx) {
        int water = !desert && idx == 0;
        if (desert && idx == 1) break;
        uint64_t r;
        setSeed(&r, ps + (uint64_t)idx + 10000 * 1);
        int x, y, z;
        if (water) {
            if (nextInt(&r, 4) != 0) continue;
            x = nextInt(&r, 16) + cx * 16; z = nextInt(&r, 16) + cz * 16;
            y = nextInt(&r, 256);
        } else {
            if (nextInt(&r, 8) != 0) continue;
            x = nextInt(&r, 16) + cx * 16; z = nextInt(&r, 16) + cz * 16;
            y = nextInt(&r, nextInt(&r, 248) + 8);
            if (!(y < 63 || nextInt(&r, 10) == 0)) continue;
        }
        T116Feat f;
        memset(&f, 0, sizeof f);
        f.kind = water ? 2 : 1;
        f.x = x; f.y = y; f.z = z;
        f.ok = lake_generate(&w, &r, x, y, z, water ? T116_WATER : T116_LAVA, &f);
        if (n < cap) out[n] = f;
        n++;
    }
    if (!(want & 2)) {
        if (result) memcpy(result, w.ch[4], sizeof(T116Chunk));
        return n;
    }
    /* UNDERGROUND_STRUCTURES (step 3): mineshafts (structure index 0; the
       OCEAN_FLOOR_WG heightmap stays at its LIQUID_CARVERS value), (buried
       treasure), fossil (desert, swamp), the dungeon attempts */
    {
        int16_t hm[256];
        ms116_heightmap(cache_get(t, cache, cx, cz), hm);
        ms116_populate(t, cx, cz, hm, &w, w_get, w_set);
    }
    {
        int idx = (bio == 2 || bio == 6) ? 3 : 2;
        uint64_t r;
        setSeed(&r, ps + (uint64_t)idx + 10000 * 3);
        for (int a = 0; a < 8; ++a) {
            int x = nextInt(&r, 16) + cx * 16, z = nextInt(&r, 16) + cz * 16, y = nextInt(&r, 256);
            T116Feat f;
            memset(&f, 0, sizeof f);
            f.kind = 0; f.x = x; f.y = y; f.z = z; f.attempt = a;
            f.ok = dungeon_generate(&w, &r, x, y, z, &f.sx, &f.sz);
            if (n < cap) out[n] = f;
            n++;
        }
    }
    if (result) memcpy(result, w.ch[4], sizeof(T116Chunk));
    return n;
}


/* ══ dungeon pre-filter: can chunk (cx, cz) have a dungeon at all? ══════════
 * Cheap and EXACT as a superset (0 = certainly none, 1 = maybe: run the full
 * population). A dungeon attempt needs 1..5 openings (air at y and y + 1 in
 * its wall ring) and a solid floor / ceiling. Without features, air below y 63
 * only comes from the cave / ravine carvers (cave air from y 11), the chunk's
 * own lakes and mineshafts; above, the noise decides. While every attempt of
 * the "all invalid" sequence is rejected, the real sequence is that one: none. */

typedef struct { uint64_t seed; int mc, cx, cz, ok, ocean; uint8_t m[8192]; } SupMask;
static __thread SupMask *SUPC = NULL;          /* [64] */
static __thread T116Chunk *SUPFAKE = NULL;

static const uint8_t *carve_superset(const T116 *t, int cx, int cz)
{
    if (!SUPC) { SUPC = calloc(64, sizeof(SupMask)); if (!SUPC) return NULL; }
    if (!SUPFAKE) { SUPFAKE = calloc(1, sizeof(T116Chunk)); if (!SUPFAKE) return NULL; }
    /* (10/10) the mask only depends on the 48 low bits (carver seeds) and on
     * the chunk being ocean or not (the cave probability): shared by the
     * sister seeds of a structure seed */
    int b4 = t116_biome4(&t->g, cx * 4, cz * 4);
    sint_init();
    const T116Biome *bi = tb(b4);
    int ocean = (bi->flags & 2) != 0;
    uint64_t low = t->seed & 0xFFFFFFFFFFFFULL;
    SupMask *e = &SUPC[((unsigned)cx * 31u + (unsigned)cz * 17u + (unsigned)ocean * 7u) & 63];
    if (e->ok && e->seed == low && e->mc == t->mc && e->cx == cx && e->cz == cz && e->ocean == ocean)
        return e->m;
    memset(e->m, 0, sizeof(e->m));
    SUPFAKE->cx = cx; SUPFAKE->cz = cz;
    SUPFAKE->bio4[2 * 9 + 2] = b4;
    Carv cv;
    memset(&cv, 0, sizeof cv);
    cv.t = t; cv.c = SUPFAKE; cv.sup = e->m; cv.mask = SUPFAKE->mask_air;
    float pcave = ocean ? 0.06666667f : 0.14285715f;
    for (int l = cx - 8; l <= cx + 8; ++l)
        for (int m = cz - 8; m <= cz + 8; ++m)
            for (int n = 0; n < 2; ++n) {
                uint64_t r;
                uint64_t ws = t->seed + (uint64_t)n;
                setSeed(&r, ws);
                uint64_t a = nextLong(&r), b = nextLong(&r);
                setSeed(&r, (uint64_t)((int64_t)l * (int64_t)a) ^ (uint64_t)((int64_t)m * (int64_t)b) ^ ws);
                int kind = n == 0 ? CV_CAVE : CV_RAVINE;
                float pr = kind == CV_CAVE ? pcave : 0.02f;
                if (!(nextFloat(&r) <= pr)) continue;
                cv.kind = kind;
                if (kind == CV_CAVE) carve_cave_src(&cv, &r, l, m);
                else carve_ravine_src(&cv, &r, l, m);
            }
    e->seed = low; e->mc = t->mc; e->cx = cx; e->cz = cz; e->ocean = ocean; e->ok = 1;
    return e->m;
}

static int sup_air(const T116 *t, int x, int y, int z)
{
    const uint8_t *m = carve_superset(t, x >> 4, z >> 4);
    if (!m) return 1;
    int i = (x & 15) | (z & 15) << 4 | y << 8;
    return (m[i >> 3] >> (i & 7)) & 1;
}

/* the exact noise density sign at one block (as t116_noise): 1 solid */
static int noise_solid(const T116 *t, const int *bio9, int bx0, int bz0, int x, int y, int z)
{
    int nx = x >> 2, nz = z >> 2, q = y >> 3;
    double c[2][2][2];
    for (int a = 0; a < 2; ++a)
        for (int b = 0; b < 2; ++b) {
            int bb[25];
            for (int n = 0; n < 5; ++n)
                for (int m = 0; m < 5; ++m)
                    bb[n * 5 + m] = bio9[(nz + b - 2 - bz0 + n) * 16 + (nx + a - 2 - bx0 + m)];
            T116Col col;
            t116_column_lazy(t, nx + a, nz + b, bb, &col);
            c[a][b][0] = col_val(t, &col, q); c[a][b][1] = col_val(t, &col, q + 1);
        }
    double yy = (double)(y & 7) / 8.0;
    double zz = j_lerp(yy, c[0][0][0], c[0][0][1]), aa = j_lerp(yy, c[1][0][0], c[1][0][1]);
    double ab = j_lerp(yy, c[0][1][0], c[0][1][1]), ac = j_lerp(yy, c[1][1][0], c[1][1][1]);
    double ag = (double)(x & 3) / 4.0, am = (double)(z & 3) / 4.0;
    double an = j_lerp(am, j_lerp(ag, zz, aa), j_lerp(ag, ab, ac));
    double ao = an / 200.0;
    ao = ao < -1.0 ? -1.0 : (ao > 1.0 ? 1.0 : ao);
    ao = ao / 2.0 - ao * ao * ao / 24.0;
    return ao > 0.0;
}

int t116_dungeons_maybe(const T116 *t, int cx, int cz)
{
    /* 1:4 biomes of the chunk ±1 (cells cx*4-6 .. cx*4+9: the noise columns
       of the attempts, x-4..x+4, need ±2 more) */
    int bio9[16 * 16];
    int bx0 = cx * 4 - 6, bz0 = cz * 4 - 6;
    {
        Range r = {4, bx0, bz0, 16, 16, 0, 1};
        int *bc = allocCache(&t->g, r);
        if (!bc) return 1;
        genBiomes(&t->g, bc, r);
        memcpy(bio9, bc, sizeof(bio9));
        free(bc);
    }
#define BIO(x4, z4) bio9[((z4) - bz0) * 16 + ((x4) - bx0)]
    /* surface builders that ADD blocks in the air (eroded badlands pillars,
       icebergs): no pre-filter around them */
    for (int k = 0; k < 256; ++k) {
        int b = bio9[k];
        if (b == 165 || b == 10 || b == 50) return 1;
    }
    int bio = BIO(cx * 4 + 2, cz * 4 + 2);
    uint64_t ps = pop_seed(t->seed, cx * 16, cz * 16);
    int desert = is_desert_id(bio);
    /* the chunk's lake boxes (any drop: down to y 0) */
    int nl = 0, lx[2], lz[2], ly[2], lyl[2];
    for (int idx = 0; idx < 2; ++idx) {
        int water = !desert && idx == 0;
        if (desert && idx == 1) break;
        uint64_t r;
        setSeed(&r, ps + (uint64_t)idx + 10000 * 1);
        int x, y, z;
        if (water) {
            if (nextInt(&r, 4) != 0) continue;
            x = nextInt(&r, 16) + cx * 16; z = nextInt(&r, 16) + cz * 16; y = nextInt(&r, 256);
        } else {
            if (nextInt(&r, 8) != 0) continue;
            x = nextInt(&r, 16) + cx * 16; z = nextInt(&r, 16) + cz * 16;
            y = nextInt(&r, nextInt(&r, 248) + 8);
            if (!(y < 63 || nextInt(&r, 10) == 0)) continue;
        }
        lx[nl] = x; lz[nl] = z; ly[nl] = y + 3;
        /* its air layers: y .. y + 3 when it cannot drop (rock under the
           decorator point), else anywhere down to y 2 */
        lyl[nl] = (y > 5 && noise_solid(t, bio9, bx0, bz0, x, y, z) && !sup_air(t, x, y, z)) ? y : 2;
        nl++;
    }
    /* the mineshaft pieces reaching the chunk ±4 */
    Ms116Box pb[256];
    int np = 0, ms_over = 0;
    {
        int x1 = cx * 16 - 4, x2 = cx * 16 + 19, z1 = cz * 16 - 4, z2 = cz * 16 + 19;
        for (int a = cx - 9; a <= cx + 9 && !ms_over; ++a)
            for (int b = cz - 9; b <= cz + 9 && !ms_over; ++b) {
                if (!ms116_has_start(t, a, b)) continue;
                Ms116Start st;
                if (!ms116_start(t, a, b, &st)) continue;
                for (int i = 0; i < st.n; ++i) {
                    const Ms116Box *q = &st.p[i].bb;
                    if (q->x1 < x1 || q->x0 > x2 || q->z1 < z1 || q->z0 > z2) continue;
                    if (np < 256) pb[np++] = *q; else ms_over = 1;
                }
                ms116_start_free(&st);
            }
        if (ms_over) return 1;
    }
    int idx = (bio == 2 || bio == 6) ? 3 : 2;
    uint64_t r;
    setSeed(&r, ps + (uint64_t)idx + 10000 * 3);
    for (int a = 0; a < 8; ++a) {
        int X = nextInt(&r, 16) + cx * 16, Z = nextInt(&r, 16) + cz * 16, Y = nextInt(&r, 256);
        int j = nextInt(&r, 2) + 2, o = nextInt(&r, 2) + 2;
        if (Y < 1 || Y > 251) continue;                     /* floor / ceiling outside: never */
        int x0 = X - j - 1, x1 = X + j + 1, z0 = Z - o - 1, z1 = Z + o + 1;
        /* a mineshaft piece or a lake box on the room: maybe */
        int near = 0;
        for (int i = 0; i < np && !near; ++i)
            near = !(pb[i].x1 < x0 || pb[i].x0 > x1 || pb[i].z1 < z0 || pb[i].z0 > z1 ||
                     pb[i].y1 < Y - 1 || pb[i].y0 > Y + 4);
        if (near) return 1;
        /* floor and ceiling under / over the spawner must be solid rock */
        if (!noise_solid(t, bio9, bx0, bz0, X, Y - 1, Z) || !noise_solid(t, bio9, bx0, bz0, X, Y + 4, Z))
            continue;
        /* an opening needs air at y and y + 1 on the ring */
        int open = 0;
        for (int sx = x0; sx <= x1 && !open; ++sx)
            for (int sz = z0; sz <= z1 && !open; ++sz) {
                if (sx != x0 && sx != x1 && sz != z0 && sz != z1) continue;
                int lk = 0;                 /* in a lake's air layers */
                for (int i = 0; i < nl && !lk; ++i)
                    lk = sx >= lx[i] && sx <= lx[i] + 15 && sz >= lz[i] && sz <= lz[i] + 15 &&
                         Y + 1 >= lyl[i] && Y <= ly[i];
                if (lk) { open = 1; break; }
                if (Y >= 62) {
                    /* maybe air: noise air, the top stone of a column under air
                       (the surface builder may remove it) or carved air */
                    int a0 = sup_air(t, sx, Y, sz) || !noise_solid(t, bio9, bx0, bz0, sx, Y, sz) ||
                             !noise_solid(t, bio9, bx0, bz0, sx, Y + 1, sz);
                    int a1 = a0 && (sup_air(t, sx, Y + 1, sz) || !noise_solid(t, bio9, bx0, bz0, sx, Y + 1, sz) ||
                                    !noise_solid(t, bio9, bx0, bz0, sx, Y + 2, sz));
                    open = a0 && a1;
                } else {
                    open = sup_air(t, sx, Y, sz) && sup_air(t, sx, Y + 1, sz);
                }
            }
        if (open) return 1;
    }
#undef BIO
    return 0;
}
