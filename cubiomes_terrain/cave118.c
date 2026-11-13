/*
 * cave118.c — (SSA 23/10) what the underground of one 1.18+ overworld chunk looks like
 * right after the CARVERS status, block by block: solid / air / water / lava.
 *
 * Used to know which cells of a structure placed later (a stronghold: generateBox
 * with skipAir) were already air, so the loot RNG of its chests can be replayed.
 *
 * Faithful port of the 1.18 → 26.2 game code:
 *   - NoiseChunk: final_density = min(squeeze(interpolated(0.64*slide(...))), noodle)
 *     + beardifier, interpolated over 4x8x4 cells (y, then x, then z, as the game);
 *   - Beardifier: BURY pieces (strongholds), passed in by the caller;
 *   - Aquifer.NoiseBasedAquifer (computeSubstance, pressure, fluid status);
 *   - the three overworld carvers (cave, cave_extra_underground, canyon), carving
 *     replaceable blocks into the aquifer's state (lava at y <= -56).
 * NOT here (yet): lakes, geodes, mineshafts, monster rooms, other structures' beards.
 * 26.3 rewrote the noise router and the aquifer: it is not covered by this file.
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "terrainnoise.h"
#include "rng.h"

#if defined(_WIN32)
  #define CV_EXPORT __declspec(dllexport)
#else
  #define CV_EXPORT __attribute__((visibility("default")))
#endif

void sampleCornerInputs(TerrainNoise *p, int x, int y, int z, double depth, double factor,
                        double jagged, double out[5]);
double cornerUpperBound(TerrainNoise *p, int x, int y, int z, double depth, double factor,
                        double jagged, double *sloped_out);
void sampleCornerInputsS(TerrainNoise *p, int x, int y, int z, double sloped, double out[5]);

enum { B_SOLID = 0, B_AIR = 1, B_WATER = 2, B_LAVA = 3 };
#define WAY_BELOW_MIN_Y (-2032 * 16)
#define SEA_LEVEL 63

typedef struct {
    TerrainNoise tn;
    int mc;
    uint64_t seed;
    DoublePerlinNoise aqBarrier, aqFlood, aqSpread, aqLava;
    PerlinNoise aqOct[8];
    uint64_t aqLo, aqHi;            /* aquifer positional random factory */
    void *work;                     /* per-context buffers (one context per thread) */
    void *ccache;                   /* (31/10) cv_top's corner columns (kept across seeds) */
    void *pscache;                  /* (31/10) preliminary surfaces (aquifers), per position */
} CaveCtx;

/* ------------------------------------------------------------------ Mth --- */
static float SIN_TABLE[65536];
static int sin_ready = 0;
static void sin_init(void) {
    if (sin_ready) return;
    for (int i = 0; i < 65536; i++) SIN_TABLE[i] = (float)sin((double)i * 3.141592653589793 * 2.0 / 65536.0);
    sin_ready = 1;
}
static int g_dbl_trig = 1;   /* 1.21.2+: Mth.sin(double); before: Mth.sin(float) */
static float msin(float f) {
    if (g_dbl_trig) return SIN_TABLE[(int)((int64_t)((double)f * 10430.378350470453) & 0xFFFF)];
    return SIN_TABLE[(int)(f * 10430.378f) & 0xFFFF];
}
static float mcos(float f) {
    if (g_dbl_trig) return SIN_TABLE[(int)((int64_t)((double)f * 10430.378350470453 + 16384.0) & 0xFFFF)];
    return SIN_TABLE[(int)(f * 10430.378f + 16384.0f) & 0xFFFF];
}
static int mfloor(double d) { int i = (int)d; return d < i ? i - 1 : i; }
static double mlerp(double t, double a, double b) { return a + t * (b - a); }
static double mclamp(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }
static double cvClampedLerp(double a, double b, double t) { return t < 0.0 ? a : (t > 1.0 ? b : mlerp(t, a, b)); }
static double cvInverseLerp(double v, double a, double b) { return (v - a) / (b - a); }
static double cvClampedMap(double v, double a, double b, double c, double d) { return cvClampedLerp(c, d, cvInverseLerp(v, a, b)); }
static double cvMap(double v, double a, double b, double c, double d) { return mlerp(cvInverseLerp(v, a, b), c, d); }
static int cvFloorDiv(int a, int b) { int q = a / b; if ((a % b != 0) && ((a < 0) != (b < 0))) q--; return q; }

/* ------------------------------------------------------- java.util.Random --- */
typedef struct { uint64_t s; } JR;
static void jr_set(JR *r, uint64_t seed) { r->s = (seed ^ 0x5DEECE66DULL) & ((1ULL << 48) - 1); }
static int jr_next(JR *r, int bits) { r->s = (r->s * 0x5DEECE66DULL + 0xB) & ((1ULL << 48) - 1); return (int)(int64_t)(r->s >> (48 - bits)); }
static int jr_int(JR *r, int n) {
    if ((n & -n) == n) return (int)(((int64_t)n * (int64_t)jr_next(r, 31)) >> 31);
    int bits, val;
    do { bits = jr_next(r, 31); val = bits % n; } while (bits - val + (n - 1) < 0);
    return val;
}
static int64_t jr_long(JR *r) { int64_t hi = (int64_t)jr_next(r, 32); int64_t lo = (int64_t)jr_next(r, 32); return (int64_t)((uint64_t)hi << 32) + lo; }
static float jr_float(JR *r) { return jr_next(r, 24) * 5.9604645E-8f; }

/* ---------------------------------------------------------------- context --- */
static int initHelper(DoublePerlinNoise *dpn, Xoroshiro xr, uint64_t m0, uint64_t m1,
                      PerlinNoise *oct, int firstOctave) {
    static const double amp[1] = {1.0};
    Xoroshiro x = {xr.lo ^ m0, xr.hi ^ m1};
    return xDoublePerlinInit(dpn, &x, oct, amp, firstOctave, 1, -1);
}

CV_EXPORT void *cv_alloc(void) { return calloc(1, sizeof(CaveCtx)); }

static int g_skip_mc;            /* (tentative: defined with its value below) */
CV_EXPORT int cv_init(void *p, int mc, uint64_t seed)
{
    CaveCtx *c = (CaveCtx *)p;
    sin_init();
    void *work = c->work, *cc = c->ccache, *ps = c->pscache;
    memset(c, 0, sizeof(*c));
    c->work = work; c->ccache = cc; c->pscache = ps;
    if (!setupTerrainNoise(&c->tn, mc < MC_1_18 ? MC_1_18 : mc, 0)) return 0;   /* 1.18 → 26.2 */
    if (!initTerrainNoise(&c->tn, seed, DIM_OVERWORLD)) return 0;
    c->mc = mc; c->seed = seed;
    /* (31/10) also for cv_top (before: set by cv_chunk only). skipSamplingAboveY
       (NoiseBasedAquifer, in the code from 26.2) only skips work: the same blocks
       for 1.18.2 → 26.1 too (checked: 160 chunks, 24 000 surfaces) — on for all */
    g_skip_mc = getenv("CV_SKIPMC") ? atoi(getenv("CV_SKIPMC")) : MC_1_18;
    Xoroshiro ws; xSetSeed(&ws, seed);
    Xoroshiro xr; xr.lo = xNextLong(&ws); xr.hi = xNextLong(&ws);
    int n = 0;
    n += initHelper(&c->aqBarrier, xr, 0xe170fda6c51d093fULL, 0x134f3e4bc3f40b0cULL, c->aqOct + n, -3);
    n += initHelper(&c->aqFlood,   xr, 0x1b5d4f85fdc2d89eULL, 0x22c7e3e611bf67c6ULL, c->aqOct + n, -7);
    n += initHelper(&c->aqSpread,  xr, 0x08f136f47cd9aaa2ULL, 0x3666182f5fa0ac6fULL, c->aqOct + n, -5);
    n += initHelper(&c->aqLava,    xr, 0x0e5121277ab51b72ULL, 0x2724d8c56aa005baULL, c->aqOct + n, -1);
    Xoroshiro aq = {xr.lo ^ 0x7bb15cc403c6ace6ULL, xr.hi ^ 0x0bdd56bc9d232691ULL};   /* fromHashOf("minecraft:aquifer") */
    c->aqLo = xNextLong(&aq); c->aqHi = xNextLong(&aq);                             /* .forkPositional() */
    return 1;
}

/* climate at block (bx,bz) (shift_a etc. evaluated at block*0.25, as the game) */
static void climateAt(CaveCtx *c, double bx, double bz, float np[4], double *erosion)
{
    BiomeNoise *bn = &c->tn.g.bn;
    double qx = bx * 0.25, qz = bz * 0.25;
    double px = qx + sampleDoublePerlin(&bn->climate[NP_SHIFT], qx, 0, qz) * 4.0;
    double pz = qz + sampleDoublePerlin(&bn->climate[NP_SHIFT], qz, qx, 0) * 4.0;
    double cv = sampleDoublePerlin(&bn->climate[NP_CONTINENTALNESS], px, 0, pz);
    double ev = sampleDoublePerlin(&bn->climate[NP_EROSION], px, 0, pz);
    double wv = sampleDoublePerlin(&bn->climate[NP_WEIRDNESS], px, 0, pz);
    np[0] = (float)cv; np[1] = (float)ev;
    /* ridges_folded in double, as the router (24/10: was float, 1e-7 off) */
    np[2] = (float)(-3.0 * (-0.3333333333333333 + fabs(-0.6666666666666666 + fabs(wv))));
    np[3] = (float)wv;
    if (erosion) *erosion = ev;
}
static double offsetAt(CaveCtx *c, const float np[4]) { return getSpline(c->tn.g.bn.sp, np) + -0.5037500262260437; }

/* router.preliminary_surface_level (find_top_surface), at a block column */
static int prelimSurface_(CaveCtx *c, int x, int z);
/* (31/10) cached per (seed, mc, x, z): every aquifer of a chunk asks ~100 of them,
 * shared with its neighbours */
typedef struct { uint64_t seed; int mc, x, z, ok, v; } PsEnt;
#define PS_N 16384
static int prelimSurface(CaveCtx *c, int x, int z)
{
    if (!c->pscache) c->pscache = calloc(PS_N, sizeof(PsEnt));
    if (!c->pscache) return prelimSurface_(c, x, z);
    uint32_t h = ((uint32_t)x * 0x9E3779B1u) ^ ((uint32_t)z * 0x85EBCA6Bu);
    h ^= h >> 13;
    PsEnt *e = &((PsEnt *)c->pscache)[h & (PS_N - 1)];
    if (e->ok && e->x == x && e->z == z && e->seed == c->seed && e->mc == c->mc) return e->v;
    int v = prelimSurface_(c, x, z);
    e->ok = 1; e->x = x; e->z = z; e->seed = c->seed; e->mc = c->mc; e->v = v;
    return v;
}

static int prelimSurface_(CaveCtx *c, int x, int z)
{
    float np[4]; climateAt(c, x, z, np, NULL);
    double offset = offsetAt(c, np);
    double factor = getSpline(c->tn.factorSpline, np);
    double ub = 128.0 + -128.0 * (0.2734375 * (1.0 / factor) + -1.0 * offset);
    ub = mclamp(ub, -40.0, 320.0);
    int top = mfloor(ub / 8.0) * 8;
    if (top <= -64) return -64;
    for (int y = top; y >= -64; y -= 8) {
        double depth = cvClampedMap(y, -64, 320, 1.5, -1.5) + offset;
        double q = depth * factor;                                  /* quarter_negative(mul(depth,factor)) */
        q = q > 0.0 ? q : q * 0.25;
        double inner = mclamp(-0.703125 + 4.0 * q, -64.0, 64.0);
        double d = 0.078125 + inner;
        d = -0.078125 + cvClampedMap(y, 240, 256, 1.0, 0.0) * d;
        d = -0.1171875 + d;
        d = 0.1171875 + cvClampedMap(y, -64, -40, 0.0, 1.0) * d;
        d = -0.390625 + d;
        if (d > 0.0) return y;
    }
    return -64;
}

/* --------------------------------------------------------------- aquifer --- */
typedef struct { int level; int type; } Fluid;     /* type: B_WATER / B_LAVA */
static int fluid_at(Fluid f, int y) { return y < f.level ? f.type : B_AIR; }
static Fluid globalFluid(int y) { Fluid f; if (y < -54) { f.level = -54; f.type = B_LAVA; } else { f.level = SEA_LEVEL; f.type = B_WATER; } return f; }

#define AQ_GX 4
#define AQ_GY 40
#define AQ_GZ 4
#define SL_N 64
typedef struct {
    CaveCtx *c;
    int cx, cz;
    int minGX, minGY, minGZ;
    int locX[AQ_GX * AQ_GY * AQ_GZ], locY[AQ_GX * AQ_GY * AQ_GZ], locZ[AQ_GX * AQ_GY * AQ_GZ];
    int locOk[AQ_GX * AQ_GY * AQ_GZ];
    Fluid st[AQ_GX * AQ_GY * AQ_GZ];   int stOk[AQ_GX * AQ_GY * AQ_GZ];
    int slQX, slQZ; int sl[SL_N * SL_N]; int slOk[SL_N * SL_N];
    int deepDark;                      /* 1.19+: no fluid in the deep dark region */
    int skipAbove;                     /* 26.x: above it, the global fluid directly */
} Aquifer;

static int aq_surface(Aquifer *a, int x, int z);
static int g_skip_mc = 1000;   /* first version with skipSamplingAboveY (set below) */

static void aq_init(Aquifer *a, CaveCtx *c, int cx, int cz)
{
    memset(a, 0, sizeof(*a));
    a->c = c; a->cx = cx; a->cz = cz;
    a->minGX = ((cx << 4) - 5) >> 4;
    a->minGY = cvFloorDiv(-64 + 1, 12) - 1;
    a->minGZ = ((cz << 4) - 5) >> 4;
    a->slQX = (cx << 2) - 28; a->slQZ = (cz << 2) - 28;
    a->deepDark = c->mc >= MC_1_19_2;
    a->skipAbove = 0x7fffffff;
    if (c->mc >= g_skip_mc) {                   /* NoiseBasedAquifer.skipSamplingAboveY */
        int maxGX = (((cx << 4) + 15 - 5) >> 4) + 1, maxGZ = (((cz << 4) + 15 - 5) >> 4) + 1;
        int x0 = a->minGX << 4, z0 = a->minGZ << 4, x1 = (maxGX << 4) + 9, z1 = (maxGZ << 4) + 9;
        int mx = -0x7fffffff;
        for (int z = z0; z <= z1; z += 4)
            for (int x = x0; x <= x1; x += 4) {
                int v = aq_surface(a, x, z);
                if (v > mx) mx = v;
            }
        int gy = cvFloorDiv(mx + 8 + 12, 12) + 1;
        a->skipAbove = gy * 12 + 11 - 1;
    }
}

static int aq_surface(Aquifer *a, int x, int z)
{
    int qx = x >> 2, qz = z >> 2;
    int i = qx - a->slQX, k = qz - a->slQZ;
    if (i >= 0 && k >= 0 && i < SL_N && k < SL_N) {
        int id = k * SL_N + i;
        if (!a->slOk[id]) { a->sl[id] = prelimSurface(a->c, qx << 2, qz << 2); a->slOk[id] = 1; }
        return a->sl[id];
    }
    return prelimSurface(a->c, qx << 2, qz << 2);
}

/* flat_cache: inside the chunk's 5x5 quart grid → value at the quart corner;
 * outside → computed at the block itself. */
static void flatXZ(Aquifer *a, int x, int z, double *fx, double *fz)
{
    int kx = (x >> 2) - (a->cx << 2), kz = (z >> 2) - (a->cz << 2);
    if (kx >= 0 && kz >= 0 && kx < 5 && kz < 5) { *fx = (x >> 2) << 2; *fz = (z >> 2) << 2; }
    else { *fx = x; *fz = z; }
}

static int isDeepDark(Aquifer *a, int x, int y, int z)
{
    double fx, fz; flatXZ(a, x, z, &fx, &fz);
    float np[4]; double ero;
    climateAt(a->c, fx, fz, np, &ero);
    double depth = cvClampedMap(y, -64, 320, 1.5, -1.5) + offsetAt(a->c, np);
    return ero < (double)-0.225f && depth > (double)0.9f;
}

static Fluid aq_computeFluid(Aquifer *a, int x, int y, int z)
{
    static const int OFF[13][2] = {{0,0},{-2,-1},{-1,-1},{0,-1},{1,-1},{-3,0},{-2,0},{-1,0},{1,0},{-2,1},{-1,1},{0,1},{1,1}};
    Fluid global = globalFluid(y);
    int lowest = 0x7fffffff;
    int top = y + 12, bottom = y - 12;
    int centerUnder = 0;
    for (int o = 0; o < 13; o++) {
        int sx = x + (OFF[o][0] << 4), sz = z + (OFF[o][1] << 4);
        int s = aq_surface(a, sx, sz);
        int adj = s + 8;
        int start = (OFF[o][0] == 0 && OFF[o][1] == 0);
        if (start && bottom > adj) return global;
        int pokes = top > adj;
        if (pokes || start) {
            Fluid g2 = globalFluid(adj);
            if (fluid_at(g2, adj) != B_AIR) {
                if (start) centerUnder = 1;
                if (pokes) return g2;
            }
        }
        if (s < lowest) lowest = s;
    }
    /* computeSurfaceLevel */
    double partially, fully;
    if (a->deepDark && isDeepDark(a, x, y, z)) { partially = -1.0; fully = -1.0; }
    else {
        int below = lowest + 8 - y;
        double ff = centerUnder ? cvClampedMap(below, 0.0, 64.0, 1.0, 0.0) : 0.0;
        double fl = mclamp(sampleDoublePerlin(&a->c->aqFlood, x, y * 0.67, z), -1.0, 1.0);
        double fullT = cvMap(ff, 1.0, 0.0, -0.3, 0.8);
        double partT = cvMap(ff, 1.0, 0.0, -0.8, 0.4);
        partially = fl - partT; fully = fl - fullT;
    }
    int level;
    if (fully > 0.0) level = global.level;
    else if (partially > 0.0) {
        int fcx = cvFloorDiv(x, 16), fcy = cvFloorDiv(y, 40), fcz = cvFloorDiv(z, 16);
        int mid = fcy * 40 + 20;
        double spread = sampleDoublePerlin(&a->c->aqSpread, fcx, fcy * 0.7142857142857143, fcz) * 10.0;
        int q = mfloor(spread / 3.0) * 3;
        int target = mid + q;
        level = lowest < target ? lowest : target;
    } else level = WAY_BELOW_MIN_Y;
    Fluid r; r.level = level; r.type = global.type;
    if (level <= -10 && level != WAY_BELOW_MIN_Y && global.type != B_LAVA) {
        double lv = sampleDoublePerlin(&a->c->aqLava, cvFloorDiv(x, 64), cvFloorDiv(y, 40) * 1.0, cvFloorDiv(z, 64));
        if (fabs(lv) > 0.3) r.type = B_LAVA;
    }
    return r;
}

static int aq_index(Aquifer *a, int gx, int gy, int gz)
{
    int i = gx - a->minGX, j = gy - a->minGY, k = gz - a->minGZ;
    if (i < 0 || j < 0 || k < 0 || i >= AQ_GX || j >= AQ_GY || k >= AQ_GZ) return -1;
    return (j * AQ_GZ + k) * AQ_GX + i;
}
static void aq_location(Aquifer *a, int gx, int gy, int gz, int *lx, int *ly, int *lz)
{
    Xoroshiro r;
    int64_t l = (int64_t)(gx * 3129871) ^ (int64_t)gz * 116129781LL ^ (int64_t)gy;
    l = l * l * 42317861LL + l * 11LL;
    l >>= 16;
    r.lo = (uint64_t)l ^ a->c->aqLo; r.hi = a->c->aqHi;
    *lx = (gx << 4) + xNextInt(&r, 10);
    *ly = gy * 12 + xNextInt(&r, 9);
    *lz = (gz << 4) + xNextInt(&r, 10);
}
static Fluid aq_status(Aquifer *a, int gx, int gy, int gz)
{
    int id = aq_index(a, gx, gy, gz);
    if (id >= 0 && a->stOk[id]) return a->st[id];
    int lx, ly, lz; aq_location(a, gx, gy, gz, &lx, &ly, &lz);
    Fluid f = aq_computeFluid(a, lx, ly, lz);
    if (id >= 0) { a->st[id] = f; a->stOk[id] = 1; }
    return f;
}
static double similarity(int d1, int d2) { return 1.0 - (double)(d2 - d1) / 25.0; }

static double pressure(Aquifer *a, int x, int y, int z, double *barrier, Fluid s1, Fluid s2)
{
    int t1 = fluid_at(s1, y), t2 = fluid_at(s2, y);
    if ((t1 == B_LAVA && t2 == B_WATER) || (t1 == B_WATER && t2 == B_LAVA)) return 2.0;
    int diff = abs(s1.level - s2.level);
    if (diff == 0) return 0.0;
    double avg = 0.5 * (double)(s1.level + s2.level);
    double above = (double)y + 0.5 - avg;
    double base = (double)diff / 2.0;
    double edge = base - fabs(above);
    double grad, cp;
    if (above > 0.0) { cp = 0.0 + edge; grad = cp > 0.0 ? cp / 1.5 : cp / 2.5; }
    else { cp = 3.0 + edge; grad = cp > 0.0 ? cp / 3.0 : cp / 10.0; }
    double nv;
    if (grad < -2.0 || grad > 2.0) nv = 0.0;
    else {
        if (isnan(*barrier)) *barrier = sampleDoublePerlin(&a->c->aqBarrier, x, y * 0.5, z);
        nv = *barrier;
    }
    return 2.0 * (nv + grad);
}

/* Aquifer.computeSubstance: B_SOLID when the game returns null */
static int aq_substance(Aquifer *a, int x, int y, int z, double density)
{
    if (density > 0.0) return B_SOLID;
    Fluid g = globalFluid(y);
    if (y > a->skipAbove) return fluid_at(g, y);
    if (fluid_at(g, y) == B_LAVA) return B_LAVA;
    int ax = (x - 5) >> 4, ay = cvFloorDiv(y + 1, 12), az = (z - 5) >> 4;
    int d1 = 0x7fffffff, d2 = 0x7fffffff, d3 = 0x7fffffff;
    int g1[3] = {0}, g2[3] = {0}, g3[3] = {0};
    for (int i = 0; i <= 1; i++)
        for (int j = -1; j <= 1; j++)
            for (int k = 0; k <= 1; k++) {
                int gx = ax + i, gy = ay + j, gz = az + k;
                int id = aq_index(a, gx, gy, gz);
                int lx, ly, lz;
                if (id >= 0 && a->locOk[id]) { lx = a->locX[id]; ly = a->locY[id]; lz = a->locZ[id]; }
                else {
                    aq_location(a, gx, gy, gz, &lx, &ly, &lz);
                    if (id >= 0) { a->locX[id] = lx; a->locY[id] = ly; a->locZ[id] = lz; a->locOk[id] = 1; }
                }
                int dx = lx - x, dy = ly - y, dz = lz - z;
                int nd = dx * dx + dy * dy + dz * dz;
                if (d1 >= nd) {
                    memcpy(g3, g2, sizeof g3); memcpy(g2, g1, sizeof g2);
                    g1[0] = gx; g1[1] = gy; g1[2] = gz;
                    d3 = d2; d2 = d1; d1 = nd;
                } else if (d2 >= nd) {
                    memcpy(g3, g2, sizeof g3);
                    g2[0] = gx; g2[1] = gy; g2[2] = gz;
                    d3 = d2; d2 = nd;
                } else if (d3 >= nd) {
                    g3[0] = gx; g3[1] = gy; g3[2] = gz;
                    d3 = nd;
                }
            }
    Fluid s1 = aq_status(a, g1[0], g1[1], g1[2]);
    double sim12 = similarity(d1, d2);
    int state = fluid_at(s1, y);
    if (sim12 <= 0.0) return state;
    if (state == B_WATER && fluid_at(globalFluid(y - 1), y - 1) == B_LAVA) return state;
    double barrier = NAN;
    Fluid s2 = aq_status(a, g2[0], g2[1], g2[2]);
    double b12 = sim12 * pressure(a, x, y, z, &barrier, s1, s2);
    if (density + b12 > 0.0) return B_SOLID;
    Fluid s3 = aq_status(a, g3[0], g3[1], g3[2]);
    double sim13 = similarity(d1, d3);
    if (sim13 > 0.0) {
        double b13 = sim12 * sim13 * pressure(a, x, y, z, &barrier, s1, s3);
        if (density + b13 > 0.0) return B_SOLID;
    }
    double sim23 = similarity(d2, d3);
    if (sim23 > 0.0) {
        double b23 = sim12 * sim23 * pressure(a, x, y, z, &barrier, s2, s3);
        if (density + b23 > 0.0) return B_SOLID;
    }
    return state;
}

/* ------------------------------------------------------------- beardifier --- */
/* (25/10) Beardifier.compute (26.2): rigid pieces (minX minY minZ maxX maxY maxZ
 * type gld; type 0 BURY, 1 BEARD_THIN, 2 BEARD_BOX, 3 ENCAPSULATE) then jigsaw
 * junctions (x groundY z), summed in that order, as the game. */
static float g_beard_kernel[24 * 24 * 24];
static int g_beard_init = 0;

static void beard_init(void)
{
    if (g_beard_init) return;
    for (int zi = 0; zi < 24; zi++)
        for (int xi = 0; xi < 24; xi++)
            for (int yi = 0; yi < 24; yi++) {
                double dx = xi - 12, dy = (double)(yi - 12) + 0.5, dz = zi - 12;
                double d2 = dx * dx + dy * dy + dz * dz;
                g_beard_kernel[zi * 576 + xi * 24 + yi] = (float)pow(2.718281828459045, -d2 / 16.0);
            }
    g_beard_init = 1;
}

static double fast_inv_sqrt(double x)
{
    double xhalf = 0.5 * x;
    int64_t i; memcpy(&i, &x, 8);
    i = 6910469410427058090LL - (i >> 1);
    memcpy(&x, &i, 8);
    x *= 1.5 - xhalf * x * x;
    return x;
}

static double beard_contrib(int dx, int dy, int dz, int yToGround)
{
    int xi = dx + 12, yi = dy + 12, zi = dz + 12;
    if (xi < 0 || xi >= 24 || yi < 0 || yi >= 24 || zi < 0 || zi >= 24) return 0.0;
    double dyo = (double)yToGround + 0.5;
    double d2 = (double)dx * dx + dyo * dyo + (double)dz * dz;
    double value = -dyo * fast_inv_sqrt(d2 / 2.0) / 2.0;
    return value * (double)g_beard_kernel[zi * 576 + xi * 24 + yi];
}

static double bury_contrib(double dx, double dy, double dz)
{
    double d = sqrt(dx * dx + dy * dy + dz * dz);
    return cvClampedMap(d, 0.0, 6.0, 1.0, 0.0);
}

static double beard_ex(const int *rig, int nrig, const int *jun, int njun, int x, int y, int z)
{
    double v = 0.0;
    for (int i = 0; i < nrig; i++) {
        const int *b = rig + 8 * i;
        int dx = b[0] - x > x - b[3] ? b[0] - x : x - b[3]; if (dx < 0) dx = 0;
        int dz = b[2] - z > z - b[5] ? b[2] - z : z - b[5]; if (dz < 0) dz = 0;
        int groundY = b[1] + b[7];
        int dyg = y - groundY;
        switch (b[6]) {
        case 0: v += bury_contrib(dx, (double)dyg / 2.0, dz); break;
        case 1: v += beard_contrib(dx, dyg, dz, dyg) * 0.8; break;
        case 2: {
            int dy = groundY - y > y - b[4] ? groundY - y : y - b[4]; if (dy < 0) dy = 0;
            v += beard_contrib(dx, dy, dz, dyg) * 0.8; break; }
        case 3: {
            int dy = b[1] - y > y - b[4] ? b[1] - y : y - b[4]; if (dy < 0) dy = 0;
            v += bury_contrib(dx / 2.0, dy / 2.0, dz / 2.0) * 0.8; break; }
        }
    }
    for (int i = 0; i < njun; i++) {
        const int *j = jun + 3 * i;
        int dx = x - j[0], dy = y - j[1], dz = z - j[2];
        v += beard_contrib(dx, dy, dz, dy) * 0.4;
    }
    return v;
}

static double beard(const int *boxes, int nbox, int x, int y, int z)
{
    double v = 0.0;
    for (int i = 0; i < nbox; i++) {
        const int *b = boxes + 6 * i;            /* minX minY minZ maxX maxY maxZ (BURY) */
        int dx = b[0] - x > x - b[3] ? b[0] - x : x - b[3]; if (dx < 0) dx = 0;
        int dz = b[2] - z > z - b[5] ? b[2] - z : z - b[5]; if (dz < 0) dz = 0;
        int dy = y - b[1];
        double hy = (double)dy / 2.0;
        double d = sqrt((double)dx * dx + hy * hy + (double)dz * dz);
        v += cvClampedMap(d, 0.0, 6.0, 1.0, 0.0);
    }
    return v;
}

/* ---------------------------------------------------------------- carvers --- */
typedef struct {
    uint8_t *out; Aquifer *aq; int cx, cz;
    uint8_t mask[384 * 256];
    int nCarved;
    uint64_t bands;            /* (31/10) the 8-block layers computed (bit cy = y -64 + 8 cy) */
} Carve;

static void carve_cell(Carve *cv, int lx, int y, int lz)
{
    int id = (y + 64) * 256 + lz * 16 + lx;
    if (!((cv->bands >> ((y + 64) >> 3)) & 1)) return;
    if (cv->mask[id]) return;
    cv->mask[id] = 1;
    uint8_t cur = cv->out[id];
    if (cur != B_SOLID && cur != B_WATER) return;          /* air / lava: not replaceable */
    int st;
    if (y <= -56) st = B_LAVA;
    else st = aq_substance(cv->aq, (cv->cx << 4) + lx, y, (cv->cz << 4) + lz, 0.0);
    if (st == B_SOLID) return;                              /* barrier: kept */
    cv->out[id] = (uint8_t)st;
    cv->nCarved++;
}

typedef int (*SkipFn)(void *ud, double xd, double yd, double zd, int y);

static void carveEllipsoid(Carve *cv, double x, double y, double z, double hr, double vr, SkipFn skip, void *ud)
{
    double mx = (cv->cx << 4) + 8, mz = (cv->cz << 4) + 8;
    double md = 16.0 + hr * 2.0;
    if (fabs(x - mx) > md || fabs(z - mz) > md) return;
    int minX = cv->cx << 4, minZ = cv->cz << 4;
    int x0 = mfloor(x - hr) - minX - 1; if (x0 < 0) x0 = 0;
    int x1 = mfloor(x + hr) - minX;     if (x1 > 15) x1 = 15;
    int y0 = mfloor(y - vr) - 1;        if (y0 < -63) y0 = -63;
    int y1 = mfloor(y + vr) + 1;        if (y1 > 312) y1 = 312;
    int z0 = mfloor(z - hr) - minZ - 1; if (z0 < 0) z0 = 0;
    int z1 = mfloor(z + hr) - minZ;     if (z1 > 15) z1 = 15;
    for (int ix = x0; ix <= x1; ix++) {
        double xd = ((double)(minX + ix) + 0.5 - x) / hr;
        for (int iz = z0; iz <= z1; iz++) {
            double zd = ((double)(minZ + iz) + 0.5 - z) / hr;
            if (xd * xd + zd * zd >= 1.0) continue;
            for (int wy = y1; wy > y0; wy--) {
                double yd = ((double)wy - 0.5 - y) / vr;
                if (skip(ud, xd, yd, zd, wy)) continue;
                carve_cell(cv, ix, wy, iz);
            }
        }
    }
}

static int canReach(Carve *cv, double x, double z, int step, int total, float th)
{
    double xd = x - ((cv->cx << 4) + 8), zd = z - ((cv->cz << 4) + 8);
    double rem = (double)(total - step);
    double rr = (double)(th + 2.0f + 16.0f);
    return xd * xd + zd * zd - rem * rem <= rr * rr;
}

static int caveSkip(void *ud, double xd, double yd, double zd, int y)
{
    (void)y; double floor_ = *(double *)ud;
    if (yd <= floor_) return 1;
    return xd * xd + yd * yd + zd * zd >= 1.0;
}

static void caveTunnel(Carve *cv, int64_t seed, double x, double y, double z, double hm, double vm,
                       float th, float yaw, float pitch, int step, int dist, double ys, double *floorLv)
{
    JR r; jr_set(&r, (uint64_t)seed);
    int split = jr_int(&r, dist / 2) + dist / 4;
    int steep = jr_int(&r, 6) == 0;
    float yr = 0.0f, xr = 0.0f;
    for (int s = step; s < dist; s++) {
        double hr = 1.5 + (double)(msin((float)3.14159265358979323846f * (float)s / (float)dist) * th);
        double vr = hr * ys;
        float cx = mcos(pitch);
        x += (double)(mcos(yaw) * cx);
        y += (double)msin(pitch);
        z += (double)(msin(yaw) * cx);
        pitch *= steep ? 0.92f : 0.7f;
        pitch += xr * 0.1f;
        yaw += yr * 0.1f;
        xr *= 0.9f;
        yr *= 0.75f;
        { float a1 = jr_float(&r), a2 = jr_float(&r), a3 = jr_float(&r); xr += (a1 - a2) * a3 * 2.0f; }
        { float a1 = jr_float(&r), a2 = jr_float(&r), a3 = jr_float(&r); yr += (a1 - a2) * a3 * 4.0f; }
        if (s == split && th > 1.0f) {
            int64_t sa = jr_long(&r); float ta = jr_float(&r) * 0.5f + 0.5f;
            caveTunnel(cv, sa, x, y, z, hm, vm, ta, yaw - 1.5707964f, pitch / 3.0f, s, dist, 1.0, floorLv);
            int64_t sb = jr_long(&r); float tb = jr_float(&r) * 0.5f + 0.5f;
            caveTunnel(cv, sb, x, y, z, hm, vm, tb, yaw + 1.5707964f, pitch / 3.0f, s, dist, 1.0, floorLv);
            return;
        }
        if (jr_int(&r, 4) == 0) continue;
        if (!canReach(cv, x, z, s, dist, th)) return;
        carveEllipsoid(cv, x, y, z, hr * hm, vr * vm, caveSkip, floorLv);
    }
}

static void caveCarve(Carve *cv, JR *r, int scx, int scz, int yMin, int yMax)
{
    int maxDist = (4 * 2 - 1) << 4;
    int a = jr_int(r, 15);
    int b = jr_int(r, a + 1);
    int count = jr_int(r, b + 1);
    for (int k = 0; k < count; k++) {
        double x = (scx << 4) + jr_int(r, 16);
        double y = jr_int(r, yMax - yMin + 1) + yMin;
        double z = (scz << 4) + jr_int(r, 16);   /* (separate statements: Java order) */
        double hm = jr_float(r) * (1.4f - 0.7f) + 0.7f;
        double vm = jr_float(r) * (1.3f - 0.8f) + 0.8f;
        double fl = jr_float(r) * (-0.4f - -1.0f) + -1.0f;
        int tunnels = 1;
        if (jr_int(r, 4) == 0) {
            double yscale = jr_float(r) * (0.9f - 0.1f) + 0.1f;
            float rad = 1.0f + jr_float(r) * 6.0f;
            double hr = 1.5 + (double)(msin(1.5707964f) * rad);
            double vr = hr * yscale;
            carveEllipsoid(cv, x + 1.0, y, z, hr, vr, caveSkip, &fl);
            tunnels += jr_int(r, 4);
        }
        for (int i = 0; i < tunnels; i++) {
            float yaw = jr_float(r) * 6.2831855f;
            float pitch = (jr_float(r) - 0.5f) / 4.0f;
            float th; { float a1 = jr_float(r), a2 = jr_float(r); th = a1 * 2.0f + a2; }
            if (jr_int(r, 10) == 0) { float a1 = jr_float(r), a2 = jr_float(r); th *= a1 * a2 * 3.0f + 1.0f; }
            int dist = maxDist - jr_int(r, maxDist / 4);
            int64_t ts = jr_long(r);
            caveTunnel(cv, ts, x, y, z, hm, vm, th, yaw, pitch, 0, dist, 1.0, &fl);
        }
    }
}

typedef struct { float wf[384]; } CanyonUD;
static int canyonSkip(void *ud, double xd, double yd, double zd, int y)
{
    CanyonUD *u = (CanyonUD *)ud;
    int yi = y - -64;
    return (xd * xd + zd * zd) * (double)u->wf[yi - 1] + yd * yd / 6.0 >= 1.0;
}

static void canyonCarve(Carve *cv, JR *r, int scx, int scz)
{
    int maxDist = (4 * 2 - 1) * 16;
    double x = (scx << 4) + jr_int(r, 16);
    int y = jr_int(r, 67 - 10 + 1) + 10;
    double z = (scz << 4) + jr_int(r, 16);
    float yaw = jr_float(r) * 6.2831855f;
    float pitch = jr_float(r) * (0.125f - -0.125f) + -0.125f;
    double ys = 3.0;
    float tf = 6.0f - 0.0f; float t1 = (tf - 2.0f) / 2.0f; float t2 = tf - t1;
    float th; { float a1 = jr_float(r), a2 = jr_float(r); th = 0.0f + a1 * t2 + a2 * t1; }
    int dist = (int)((float)maxDist * (jr_float(r) * (1.0f - 0.75f) + 0.75f));
    int64_t seed = jr_long(r);

    JR q; jr_set(&q, (uint64_t)seed);
    CanyonUD u; float f = 1.0f;
    for (int j = 0; j < 384; j++) {
        if (j == 0 || jr_int(&q, 3) == 0) { float a1 = jr_float(&q), a2 = jr_float(&q); f = 1.0f + a1 * a2; }
        u.wf[j] = f * f;
    }
    float yr = 0.0f, xr = 0.0f;
    double px = x, py = y, pz = z;
    for (int s = 0; s < dist; s++) {
        double hr = 1.5 + (double)(msin((float)s * 3.1415927f / (float)dist) * th);
        double vr = hr * ys;
        hr *= (double)(jr_float(&q) * (1.0f - 0.75f) + 0.75f);
        {
            float vm = 1.0f - fabsf(0.5f - (float)s / (float)dist) * 2.0f;
            float fac = 1.0f + 0.0f * vm;
            vr = (double)fac * vr * (double)(jr_float(&q) * (1.0f - 0.75f) + 0.75f);
        }
        float xc = mcos(pitch), xs = msin(pitch);
        px += (double)(mcos(yaw) * xc);
        py += (double)xs;
        pz += (double)(msin(yaw) * xc);
        pitch *= 0.7f;
        pitch += xr * 0.05f;
        yaw += yr * 0.05f;
        xr *= 0.8f;
        yr *= 0.5f;
        { float a1 = jr_float(&q), a2 = jr_float(&q), a3 = jr_float(&q); xr += (a1 - a2) * a3 * 2.0f; }
        { float a1 = jr_float(&q), a2 = jr_float(&q), a3 = jr_float(&q); yr += (a1 - a2) * a3 * 4.0f; }
        if (jr_int(&q, 4) == 0) continue;
        if (!canReach(cv, px, pz, s, dist, th)) return;
        carveEllipsoid(cv, px, py, pz, hr, vr, canyonSkip, &u);
    }
}

static void largeFeatureSeed(JR *r, uint64_t seed, int cx, int cz)
{
    jr_set(r, seed);
    int64_t a = jr_long(r), b = jr_long(r);
    int64_t k = (int64_t)cx * a ^ (int64_t)cz * b ^ (int64_t)seed;
    jr_set(r, (uint64_t)k);
}

/* ----------------------------------------------------------------- export --- */
typedef struct {
    double cor[5][5][49][5]; Aquifer aq; Carve cv;
    Aquifer taq; int ta_ok, ta_cx, ta_cz, ta_mc; uint64_t ta_seed;   /* (31/10) cv_top's, per chunk */
    void *priv;                      /* (31/10) 25 private corner columns (cc_pin) */
} Work;

CV_EXPORT void cv_free(void *p) { if (p) { if (((CaveCtx *)p)->work) free(((Work *)((CaveCtx *)p)->work)->priv); free(((CaveCtx *)p)->work); free(((CaveCtx *)p)->ccache); free(((CaveCtx *)p)->pscache); free(p); } }

/* out[(y+64)*256 + z*16 + x] for the chunk (cx,cz): 0 solid, 1 air, 2 water, 3 lava.
 * boxes: nbox BURY boxes (minX minY minZ maxX maxY maxZ). flags: bit0 = ignore the
 * boxes (no beardifier), bit1 = no carvers. Returns the number of carved cells. */
/* (31/10) the corner columns of cv_top, computed lazily from the top down and kept
 * (a jigsaw village asks for hundreds of nearby columns: 4 × 4 share their corners) */
typedef struct {
    uint64_t seed; int mc, bx, bz, low;      /* low: lowest row with its bound (49: none) */
    double dep, fac, jag;
    uint64_t full;                           /* bit r: v[r] computed */
    double ub[49];                           /* an upper bound of v[r][0] (cornerUpperBound) */
    double sl[49];                           /* the sloped cheese of row r */
    double v[49][5];
} CornerCol;
#define CC_N 4096
static void cc_init(CaveCtx *c, CornerCol *e, int bx, int bz);
static inline uint32_t cc_slot(int bx, int bz);

static CornerCol *cc_get(CaveCtx *c, int bx, int bz)
{
    if (!c->ccache) {
        c->ccache = calloc(CC_N, sizeof(CornerCol));
        if (!c->ccache) return NULL;
        for (int i = 0; i < CC_N; i++) ((CornerCol *)c->ccache)[i].low = -1;
    }
    CornerCol *e = &((CornerCol *)c->ccache)[cc_slot(bx, bz)];
    if (e->low >= 0 && e->seed == c->seed && e->mc == c->mc && e->bx == bx && e->bz == bz) return e;
    cc_init(c, e, bx, bz);
    return e;
}

static inline uint32_t cc_slot(int bx, int bz)
{
    uint32_t h = ((uint32_t)bx * 0x9E3779B1u) ^ ((uint32_t)bz * 0x85EBCA6Bu);
    h ^= h >> 15;
    return h & (CC_N - 1);
}

/* the n corners a chunk / column needs, pinned: one sharing the cache slot of an
 * earlier one gets a private entry (priv[i]) instead of evicting it at every row */
static int cc_pin(CaveCtx *c, const int *bx, const int *bz, int n, CornerCol **out, CornerCol *priv)
{
    for (int i = 0; i < n; i++) {
        uint32_t si = cc_slot(bx[i], bz[i]);
        int dup = 0;
        for (int j = 0; j < i; j++)
            if (cc_slot(bx[j], bz[j]) == si && (bx[j] != bx[i] || bz[j] != bz[i])) { dup = 1; break; }
        if (dup) { cc_init(c, &priv[i], bx[i], bz[i]); out[i] = &priv[i]; }
        else if (!(out[i] = cc_get(c, bx[i], bz[i]))) return 0;
    }
    return 1;
}

static void cc_init(CaveCtx *c, CornerCol *e, int bx, int bz)
{
    e->seed = c->seed; e->mc = c->mc; e->bx = bx; e->bz = bz; e->low = 49; e->full = 0;
    float np[4]; climateAt(c, bx, bz, np, NULL);
    e->dep = offsetAt(c, np);
    e->fac = getSpline(c->tn.factorSpline, np);
    double j = sampleDoublePerlin(&c->tn.noises[OTP_JAGGED], bx * 1500.0, 0, bz * 1500.0);
    j = j >= 0.0 ? j : j / 2.0;
    e->jag = j * getSpline(c->tn.jaggednessSpline, np);
}

static double cc_ub(CaveCtx *c, CornerCol *e, int cy)
{
    while (e->low > cy) {
        int r = e->low - 1;
        e->ub[r] = cornerUpperBound(&c->tn, e->bx, -64 + r * 8, e->bz, e->dep, e->fac, e->jag, &e->sl[r]);
        e->low = r;
    }
    return e->ub[cy];
}

static const double *cc_row(CaveCtx *c, CornerCol *e, int cy)
{
    if (!(e->full >> cy & 1)) {
        cc_ub(c, e, cy);                         /* its sloped cheese, computed once */
        sampleCornerInputsS(&c->tn, e->bx, -64 + cy * 8, e->bz, e->sl[cy], e->v[cy]);
        e->full |= 1ULL << cy;
    }
    return e->v[cy];
}

static int cv_chunk_impl(void *p, int cx, int cz, const int *boxes, int nbox,
                         const int *rig, int nrig, const int *jun, int njun, uint8_t *out, int flags,
                         uint64_t bands);

CV_EXPORT int cv_chunk(void *p, int cx, int cz, const int *boxes, int nbox, uint8_t *out, int flags)
{
    return cv_chunk_impl(p, cx, cz, boxes, nbox, NULL, 0, NULL, 0, out, flags, ~0ULL);
}

/* (25/10) with a full Beardifier: rig = nrig × (minX minY minZ maxX maxY maxZ type
 * gld), jun = njun × (x groundY z). */
/* (31/10) cv_chunk for the 8-block layers of `bands` only (bit cy: y -64 + 8 cy ..
 * +7); the other layers of out are left untouched. Same blocks as cv_chunk there. */
CV_EXPORT int cv_chunk_bands(void *p, int cx, int cz, const int *boxes, int nbox, uint8_t *out, int flags,
                             uint64_t bands)
{
    return cv_chunk_impl(p, cx, cz, boxes, nbox, NULL, 0, NULL, 0, out, flags, bands);
}

CV_EXPORT int cv_chunk_ex(void *p, int cx, int cz, const int *rig, int nrig, const int *jun,
                          int njun, uint8_t *out, int flags)
{
    return cv_chunk_impl(p, cx, cz, NULL, 0, rig, nrig, jun, njun, out, flags, ~0ULL);
}

static int cv_chunk_impl(void *p, int cx, int cz, const int *boxes, int nbox,
                         const int *rig, int nrig, const int *jun, int njun, uint8_t *out, int flags,
                         uint64_t bands)
{
    beard_init();
    CaveCtx *c = (CaveCtx *)p;
    g_dbl_trig = c->mc >= MC_1_21_3;
    g_skip_mc = getenv("CV_SKIPMC") ? atoi(getenv("CV_SKIPMC")) : MC_1_18;
    if (!c->work) c->work = calloc(1, sizeof(Work));
    if (!c->work) return -1;
    Work *w = (Work *)c->work;
    double (*cor)[5][49][5] = w->cor;
    /* (31/10) the corner columns from cv_top's cache (shared with the neighbours);
       the cells above the sea and the aquifer's skipAbove whose corners are all
       below 0 (cornerUpperBound) are air without the cave / noodle noises */
    CornerCol *col[25];
    {
        int bxs[25], bzs[25];
        for (int i = 0; i < 5; i++)
            for (int k = 0; k < 5; k++) {
                bxs[i * 5 + k] = ((cx << 2) + i) << 2; bzs[i * 5 + k] = ((cz << 2) + k) << 2;
            }
        if (!w->priv) w->priv = calloc(25, sizeof(CornerCol));
        if (!w->priv || !cc_pin(c, bxs, bzs, 25, col, (CornerCol *)w->priv)) return -1;
    }
    Aquifer *aqp = &w->aq;
    aq_init(aqp, c, cx, cz);
    int beards = (nbox || nrig || njun) && !(flags & 1);
    static const uint8_t AIRV = B_AIR;
    for (int cy = 47; cy >= 0; cy--) {
        int y0 = -64 + cy * 8;
        int skip = 0;
        if (!((bands >> cy) & 1)) continue;          /* (31/10) band not asked: left as is */
        if (!beards && y0 > SEA_LEVEL && y0 > aqp->skipAbove) {
            double m = -INFINITY;
            for (int i = 0; i < 5 && m <= 0.0; i++)
                for (int k = 0; k < 5 && m <= 0.0; k++) {
                    CornerCol *e = col[i * 5 + k];
                    m = fmax(m, fmax(cc_ub(c, e, cy + 1), cc_ub(c, e, cy)));
                }
            skip = m <= 0.0;
        }
        if (skip) {
            memset(out + (y0 + 64) * 256, AIRV, 8 * 256);
            continue;
        }
        for (int i = 0; i < 5; i++)
            for (int k = 0; k < 5; k++) {
                CornerCol *e = col[i * 5 + k];
                memcpy(cor[i][k][cy], cc_row(c, e, cy), sizeof cor[i][k][cy]);
                memcpy(cor[i][k][cy + 1], cc_row(c, e, cy + 1), sizeof cor[i][k][cy + 1]);
            }
        for (int ry = 0; ry < 8; ry++) {
            int y = y0 + ry;
            double ty = ry / 8.0;
            for (int lx = 0; lx < 16; lx++) {
                int ix = lx >> 2; double tx = (lx & 3) / 4.0;
                for (int lz = 0; lz < 16; lz++) {
                    int iz = lz >> 2; double tz = (lz & 3) / 4.0;
                    double v[5];
                    for (int n = 0; n < 5; n++) {
                        double a00 = mlerp(ty, cor[ix][iz][cy][n],     cor[ix][iz][cy + 1][n]);
                        double a10 = mlerp(ty, cor[ix + 1][iz][cy][n], cor[ix + 1][iz][cy + 1][n]);
                        double a01 = mlerp(ty, cor[ix][iz + 1][cy][n], cor[ix][iz + 1][cy + 1][n]);
                        double a11 = mlerp(ty, cor[ix + 1][iz + 1][cy][n], cor[ix + 1][iz + 1][cy + 1][n]);
                        double z0 = mlerp(tx, a00, a10), z1 = mlerp(tx, a01, a11);
                        v[n] = mlerp(tz, z0, z1);
                    }
                    double s = mclamp(v[0], -1.0, 1.0);
                    s = s / 2.0 - s * s * s / 24.0;
                    double nd = (v[1] >= -1000000.0 && v[1] < 0.0) ? 64.0
                              : v[2] + 1.5 * fmax(fabs(v[3]), fabs(v[4]));
                    double d = fmin(s, nd);
                    int wx = (cx << 4) + lx, wz = (cz << 4) + lz;
                    if (nbox && !(flags & 1)) d += beard(boxes, nbox, wx, y, wz);
                    if ((nrig || njun) && !(flags & 1)) d += beard_ex(rig, nrig, jun, njun, wx, y, wz);
                    out[(y + 64) * 256 + lz * 16 + lx] = (uint8_t)aq_substance(aqp, wx, y, wz, d);
                }
            }
        }
    }
    if (flags & 2) return 0;
    Carve *cvp = &w->cv;
    memset(cvp->mask, 0, sizeof cvp->mask);
    cvp->out = out; cvp->aq = aqp; cvp->cx = cx; cvp->cz = cz; cvp->nCarved = 0; cvp->bands = bands;
    JR r;
    for (int dx = -8; dx <= 8; dx++)
        for (int dz = -8; dz <= 8; dz++) {
            int scx = cx + dx, scz = cz + dz;
            for (int l = 0; l < 3; l++) {
                largeFeatureSeed(&r, c->seed + (uint64_t)l, scx, scz);
                float pr = l == 0 ? 0.15f : (l == 1 ? 0.07f : 0.01f);
                if (!(jr_float(&r) <= pr)) continue;
                if (l == 0) caveCarve(cvp, &r, scx, scz, -56, 180);
                else if (l == 1) caveCarve(cvp, &r, scx, scz, -56, 47);
                else canyonCarve(cvp, &r, scx, scz);
            }
        }
    return cvp->nCarved;
}

/* (24/10) highest non-air block of a column straight after the NOISE status
 * (= ChunkGenerator.getFirstOccupiedHeight(WORLD_SURFACE_WG): solid or fluid,
 * no carvers, no structures). Exact per-block density (squeeze / noodle per block),
 * aquifer included. Returns -65 if the column is empty. */
static int cv_top(void *p, int x, int z, int solid_only);
CV_EXPORT int cv_surface(void *p, int x, int z) { return cv_top(p, x, z, 0); }
/* highest block with density > 0 (solid), as terrain.exact_y means it */
CV_EXPORT int cv_solid_top(void *p, int x, int z) { return cv_top(p, x, z, 1); }
/* density > 0 only (no aquifer barriers): what a GPU-friendly surface sees */
CV_EXPORT int cv_density_top(void *p, int x, int z) { return cv_top(p, x, z, 2); }

static int cv_top(void *p, int x, int z, int solid_only)
{
    CaveCtx *c = (CaveCtx *)p;
    if (!c->work) c->work = calloc(1, sizeof(Work));
    if (!c->work) return -65;
    Work *w = (Work *)c->work;
    int qx0 = x >> 2, qz0 = z >> 2;
    double tx = (x & 3) / 4.0, tz = (z & 3) / 4.0;
    CornerCol *col[4], priv[4];
    {
        int bxs[4] = {qx0 << 2, qx0 << 2, (qx0 + 1) << 2, (qx0 + 1) << 2};
        int bzs[4] = {qz0 << 2, (qz0 + 1) << 2, qz0 << 2, (qz0 + 1) << 2};
        if (!cc_pin(c, bxs, bzs, 4, col, priv)) return -65;
    }
    /* (31/10) the chunk's aquifer kept between calls (its skipAbove scan: ~100
       preliminary surfaces) — deterministic, only lazily filled caches inside */
    Aquifer *aqp = &w->taq;
    if (!(w->ta_ok && w->ta_cx == (x >> 4) && w->ta_cz == (z >> 4) && w->ta_seed == c->seed &&
          w->ta_mc == c->mc)) {
        aq_init(aqp, c, x >> 4, z >> 4);
        w->ta_ok = 1; w->ta_cx = x >> 4; w->ta_cz = z >> 4; w->ta_seed = c->seed; w->ta_mc = c->mc;
    }
    double top[2][2][5], bot[2][2][5];
    int have_top = 0;
    for (int cy = 47; cy >= 0; cy--) {
        int y0 = -64 + cy * 8;
        /* (31/10) a cell above the sea and the aquifer's skipAbove whose 8 corners'
           upper bounds are <= 0 is all air: no cave / noodle noise there */
        if (y0 > SEA_LEVEL && y0 > aqp->skipAbove) {
            double m = -INFINITY;
            for (int i = 0; i < 2 && m <= 0.0; i++)
                for (int k = 0; k < 2 && m <= 0.0; k++) {
                    CornerCol *e = col[i * 2 + k];
                    double a = cc_ub(c, e, cy + 1), b = cc_ub(c, e, cy);
                    m = fmax(m, fmax(a, b));
                }
            if (m <= 0.0) { have_top = 0; continue; }
        }
        if (!have_top)
            for (int i = 0; i < 2; i++)
                for (int k = 0; k < 2; k++) {
                    CornerCol *e = col[i * 2 + k];
                    memcpy(top[i][k], cc_row(c, e, cy + 1), sizeof top[i][k]);
                }
        for (int i = 0; i < 2; i++)
            for (int k = 0; k < 2; k++) {
                CornerCol *e = col[i * 2 + k];
                memcpy(bot[i][k], cc_row(c, e, cy), sizeof bot[i][k]);
            }
        for (int ry = 7; ry >= 0; ry--) {
            double ty = ry / 8.0, v[5];
            for (int n = 0; n < 5; n++) {
                double a00 = mlerp(ty, bot[0][0][n], top[0][0][n]);
                double a10 = mlerp(ty, bot[1][0][n], top[1][0][n]);
                double a01 = mlerp(ty, bot[0][1][n], top[0][1][n]);
                double a11 = mlerp(ty, bot[1][1][n], top[1][1][n]);
                v[n] = mlerp(tz, mlerp(tx, a00, a10), mlerp(tx, a01, a11));
            }
            double s = mclamp(v[0], -1.0, 1.0);
            s = s / 2.0 - s * s * s / 24.0;
            double nd = (v[1] >= -1000000.0 && v[1] < 0.0) ? 64.0 : v[2] + 1.5 * fmax(fabs(v[3]), fabs(v[4]));
            double d = fmin(s, nd);
            int y = y0 + ry;
            if (solid_only == 2) { if (d > 0.0) return y; continue; }
            { int st = aq_substance(aqp, x, y, z, d); if (solid_only ? st == B_SOLID : st != B_AIR) return y; }
        }
        memcpy(top, bot, sizeof top);
        have_top = 1;
    }
    return -65;
}

/* (24/10) debug: router.final_density at one cell corner (x, z multiples of 4,
 * y multiple of 8), as the game computes it at a single point. out[0..4]: inputs. */
CV_EXPORT double cv_corner_final(void *p, int x, int y, int z, double *out)
{
    CaveCtx *c = (CaveCtx *)p;
    float np[4]; climateAt(c, x, z, np, NULL);
    double depth = offsetAt(c, np);
    double factor = getSpline(c->tn.factorSpline, np);
    double jag = sampleDoublePerlin(&c->tn.noises[OTP_JAGGED], x * 1500.0, 0, z * 1500.0);
    jag = jag >= 0.0 ? jag : jag / 2.0;
    jag *= getSpline(c->tn.jaggednessSpline, np);
    double v[5];
    sampleCornerInputs(&c->tn, x, y, z, depth, factor, jag, v);
    if (out) memcpy(out, v, sizeof v);
    double s = mclamp(v[0], -1.0, 1.0);
    s = s / 2.0 - s * s * s / 24.0;
    double nd = (v[1] >= -1000000.0 && v[1] < 0.0) ? 64.0 : v[2] + 1.5 * fmax(fabs(v[3]), fabs(v[4]));
    return fmin(s, nd);
}

/* (24/10) debug: overworld/offset, factor and jaggedness at a block column */
CV_EXPORT void cv_column_params(void *p, int x, int z, double *out)
{
    CaveCtx *c = (CaveCtx *)p;
    float np[4]; double ero;
    climateAt(c, x, z, np, &ero);
    out[0] = offsetAt(c, np);
    out[1] = getSpline(c->tn.factorSpline, np);
    out[2] = getSpline(c->tn.jaggednessSpline, np);
    out[3] = ero; out[4] = np[0]; out[5] = np[1]; out[6] = np[2]; out[7] = np[3];
}

/* ───────────── (31/10) amethyst geodes: the 33³ distance field (prefeatures118._geode) ─────────────
 * perm: 2 × 256 (the two ImprovedNoise of the geode noise), off: 2 × 3 (xo, yo, zo);
 * pts: npts × (x, y, z, offset); cps: ncps × (x, y, z). Fills, for every cell of the
 * cube [o - 16, o + 16]³ in (z, y, x) order whose S >= d24: its index, S and CS (the
 * same IEEE operations, in the same order, as the numpy code). Returns the count. */
static const double GEO_GRAD[16][3] = {{1,1,0},{-1,1,0},{1,-1,0},{-1,-1,0},{1,0,1},{-1,0,1},{1,0,-1},{-1,0,-1},
    {0,1,1},{0,-1,1},{0,1,-1},{0,-1,-1},{1,1,0},{0,-1,1},{-1,1,0},{0,-1,-1}};
static inline double geo_gd(int h, double x, double y, double z)
{ const double *g = GEO_GRAD[h & 15]; return g[0] * x + g[1] * y + g[2] * z; }
static inline double geo_ss(double t) { return t * t * t * (t * (t * 6.0 - 15.0) + 10.0); }
static inline double geo_lerp(double t, double a, double b) { return a + t * (b - a); }
static double geo_noise(const int32_t *P, const double *o, double x, double y, double z)
{
    double d = x + o[0], e = y + o[1], f = z + o[2];
    double fi = floor(d), fj = floor(e), fk = floor(f);
    double g = d - fi, h = e - fj, l = f - fk;
    int i = (int)(int64_t)fi, j = (int)(int64_t)fj, k = (int)(int64_t)fk;
    int a = P[i & 255], b = P[(i + 1) & 255];
    int c0 = P[(a + j) & 255], c1 = P[(a + j + 1) & 255], c2 = P[(b + j) & 255], c3 = P[(b + j + 1) & 255];
    k &= 255;
    double v000 = geo_gd(P[(c0 + k) & 255], g, h, l);
    double v100 = geo_gd(P[(c2 + k) & 255], g - 1, h, l);
    double v010 = geo_gd(P[(c1 + k) & 255], g, h - 1, l);
    double v110 = geo_gd(P[(c3 + k) & 255], g - 1, h - 1, l);
    double v001 = geo_gd(P[(c0 + k + 1) & 255], g, h, l - 1);
    double v101 = geo_gd(P[(c2 + k + 1) & 255], g - 1, h, l - 1);
    double v011 = geo_gd(P[(c1 + k + 1) & 255], g, h - 1, l - 1);
    double v111 = geo_gd(P[(c3 + k + 1) & 255], g - 1, h - 1, l - 1);
    double s = geo_ss(g), t = geo_ss(h), u = geo_ss(l);
    return geo_lerp(u, geo_lerp(t, geo_lerp(s, v000, v100), geo_lerp(s, v010, v110)),
                    geo_lerp(t, geo_lerp(s, v001, v101), geo_lerp(s, v011, v111)));
}
static inline double geo_wrap(double d) { return d - floor(d / 3.3554432E7 + 0.5) * 3.3554432E7; }
static inline double geo_isq(double d, int fast)
{
    if (!fast)
        return 1.0 / sqrt(d);
    double e = 0.5 * d;
    int64_t l; memcpy(&l, &d, 8);
    l = (int64_t)6910469410427058090LL - (l >> 1);
    memcpy(&d, &l, 8);
    return d * (1.5 - e * d * d);
}
CV_EXPORT int cv_geode_eval(const int32_t *perm, const double *off, int ox, int oy, int oz,
                            int npts, const int *pts, int ncps, const int *cps, int fast, int centred,
                            double d24, int32_t *hits, double *sv, double *csv, int cap)
{
    const double q = 0.0625, c5 = centred ? 0.5 : 0.0;
    int n = 0;
    for (int iz = 0; iz < 33; iz++)
        for (int iy = 0; iy < 33; iy++)
            for (int ix = 0; ix < 33; ix++) {
                double X = (double)(ox - 16 + ix), Y = (double)(oy - 16 + iy), Z = (double)(oz - 16 + iz);
                double a = geo_noise(perm, off, geo_wrap(X * q), geo_wrap(Y * q), geo_wrap(Z * q));
                double x2 = X * 1.0181268882175227, y2 = Y * 1.0181268882175227, z2 = Z * 1.0181268882175227;
                double b = geo_noise(perm + 256, off + 3, geo_wrap(x2 * q), geo_wrap(y2 * q), geo_wrap(z2 * q));
                double nv = (a + b) * (0.16666666666666666 / 0.2) * 0.05;
                double Xc = X + c5, Yc = Y + c5, Zc = Z + c5;
                double S = 0.0;
                for (int p = 0; p < npts; p++) {
                    double dx = Xc - pts[4 * p], dy = Yc - pts[4 * p + 1], dz = Zc - pts[4 * p + 2];
                    S = S + (geo_isq(dx * dx + dy * dy + dz * dz + pts[4 * p + 3], fast) + nv);
                }
                if (!(S >= d24))
                    continue;
                double CS = 0.0;
                for (int p = 0; p < ncps; p++) {
                    double dx = Xc - cps[3 * p], dy = Yc - cps[3 * p + 1], dz = Zc - cps[3 * p + 2];
                    CS = CS + (geo_isq(dx * dx + dy * dy + dz * dz + 2, fast) + nv);
                }
                if (n < cap) { hits[n] = (iz * 33 + iy) * 33 + ix; sv[n] = S; csv[n] = CS; }
                n++;
            }
    return n;
}

/* ───────────── (31/10) 1.18+ dungeons of one chunk (prefeatures118._dungeons) ─────────────
 * ch: the 3 × 3 chunk buffers around (cx, cz) [(dz + 1) * 3 + dx + 1], codes of
 * prefeatures118 ([((y + 64) * 16 + z) * 16 + x]), changed in place; solid / cannot:
 * bit masks of the codes (_LEGACY_SOLID, _CANNOT_REPLACE). The random: setFeatureSeed
 * (decoration seed + index + 30000) of monster_room (index 2, 10 tries, y 0..319) then
 * monster_room_deep (3, 4 tries, y -58..-1), WorldgenRandom over Xoroshiro (next(bits):
 * the high bits). out: per dungeon 15 int64 (x, y, z, j, o, mob, nchests, 2 × (x, y, z,
 * loot seed)). Returns the number of dungeons. */
enum { DG_AIR = 1, DG_SPAWNER = 11, DG_COBBLE = 12, DG_MOSSY = 13, DG_CHEST = 14 };
typedef struct { uint8_t **ch; int cx0, cz0; uint32_t solid, cannot; } DgW;
static inline uint8_t *dg_at(DgW *w, int x, int y, int z)
{
    if (y < -64 || y > 319) return NULL;
    int i = (x >> 4) - w->cx0, k = (z >> 4) - w->cz0;
    if (i < 0 || i > 2 || k < 0 || k > 2 || !w->ch[k * 3 + i]) return NULL;
    return w->ch[k * 3 + i] + ((((y + 64) << 4) | (z & 15)) << 4 | (x & 15));
}
static inline int dg_get(DgW *w, int x, int y, int z) { uint8_t *p = dg_at(w, x, y, z); return p ? *p : DG_AIR; }
static inline void dg_set(DgW *w, int x, int y, int z, int v) { uint8_t *p = dg_at(w, x, y, z); if (p) *p = (uint8_t)v; }
static inline int dg_solid(DgW *w, int b) { return b < 32 && ((w->solid >> b) & 1); }
static inline int dg_cannot(DgW *w, int b) { return b < 32 && ((w->cannot >> b) & 1); }
static inline int wg_next(Xoroshiro *r, int bits) { return (int)(int64_t)(xNextLong(r) >> (64 - bits)); }
static int wg_int(Xoroshiro *r, int n)
{
    int m = n - 1;
    if ((n & m) == 0) return (int)(((int64_t)n * wg_next(r, 31)) >> 31);
    for (;;) {
        int u = wg_next(r, 31), v = u % n;
        if ((int64_t)u - v + m < ((int64_t)1 << 31)) return v;
    }
}
static int64_t wg_long(Xoroshiro *r)
{
    int32_t a = (int32_t)(uint32_t)(xNextLong(r) >> 32);
    int32_t b = (int32_t)(uint32_t)(xNextLong(r) >> 32);
    return (int64_t)((uint64_t)(int64_t)a << 32) + (int64_t)b;
}
static int dg_place(DgW *w, Xoroshiro *r, int ox, int oy, int oz, int64_t *rec)
{
    int j = wg_int(r, 2) + 2, k = -j - 1, l = j + 1;
    int o = wg_int(r, 2) + 2, p = -o - 1, q = o + 1;
    int n = 0;
    for (int x = k; x <= l; x++)
        for (int z = p; z <= q; z++) {
            if (!dg_solid(w, dg_get(w, ox + x, oy - 1, oz + z)) || !dg_solid(w, dg_get(w, ox + x, oy + 4, oz + z)))
                return 0;
            if ((x == k || x == l || z == p || z == q) && dg_get(w, ox + x, oy, oz + z) == DG_AIR &&
                dg_get(w, ox + x, oy + 1, oz + z) == DG_AIR)
                n++;
        }
    if (n < 1 || n > 5) return 0;
    for (int x = k; x <= l; x++)
        for (int y = 3; y >= -1; y--)
            for (int z = p; z <= q; z++) {
                int X = ox + x, Y = oy + y, Z = oz + z, b = dg_get(w, X, Y, Z);
                if (x == k || x == l || y == -1 || y == 4 || z == p || z == q) {
                    if (Y >= -64 && !dg_solid(w, dg_get(w, X, Y - 1, Z)))
                        dg_set(w, X, Y, Z, DG_AIR);
                    else if (dg_solid(w, b) && b != DG_CHEST) {
                        if (y == -1 && wg_int(r, 4) != 0) {
                            if (!dg_cannot(w, b)) dg_set(w, X, Y, Z, DG_MOSSY);
                        } else if (!dg_cannot(w, b))
                            dg_set(w, X, Y, Z, DG_COBBLE);
                    }
                } else if (b != DG_CHEST && b != DG_SPAWNER) {
                    if (!dg_cannot(w, b)) dg_set(w, X, Y, Z, DG_AIR);
                }
            }
    int nch = 0;
    for (int i = 0; i < 2; i++)
        for (int t = 0; t < 3; t++) {
            int X = ox + wg_int(r, j * 2 + 1) - j;
            int Z = oz + wg_int(r, o * 2 + 1) - o;
            if (dg_get(w, X, oy, Z) != DG_AIR) continue;
            int c = dg_solid(w, dg_get(w, X, oy, Z - 1)) + dg_solid(w, dg_get(w, X + 1, oy, Z)) +
                    dg_solid(w, dg_get(w, X, oy, Z + 1)) + dg_solid(w, dg_get(w, X - 1, oy, Z));
            if (c != 1) continue;
            if (!dg_cannot(w, dg_get(w, X, oy, Z))) dg_set(w, X, oy, Z, DG_CHEST);
            int64_t ls = wg_long(r);
            int64_t *cr = rec + 7 + 4 * nch;
            cr[0] = X; cr[1] = oy; cr[2] = Z; cr[3] = ls;
            nch++;
            break;
        }
    if (!dg_cannot(w, dg_get(w, ox, oy, oz))) dg_set(w, ox, oy, oz, DG_SPAWNER);
    int mob = wg_int(r, 4);
    rec[0] = ox; rec[1] = oy; rec[2] = oz; rec[3] = j; rec[4] = o; rec[5] = mob; rec[6] = nch;
    return 1;
}
/* (31/10) the 8-block layers (bits) each of the 3 × 3 chunks needs for one attempt
 * (x ox ± (j + 1), z oz ± (o + 1), y oy - 2 .. oy + 4: the room, its floor's support) */
static void dg_need(int cx, int cz, int ox, int oy, int oz, int j, int o, uint64_t *need)
{
    int ylo = oy - 2 < -64 ? -64 : oy - 2, yhi = oy + 4 > 319 ? 319 : oy + 4;
    if (ylo > yhi) return;
    uint64_t m = 0;
    for (int b = (ylo + 64) >> 3; b <= (yhi + 64) >> 3; b++) m |= 1ULL << b;
    for (int X = (ox - j - 1) >> 4; X <= (ox + j + 1) >> 4; X++)
        for (int Z = (oz - o - 1) >> 4; Z <= (oz + o + 1) >> 4; Z++) {
            int i = X - cx + 1, k = Z - cz + 1;
            if (i >= 0 && i < 3 && k >= 0 && k < 3) need[k * 3 + i] |= m;
        }
}
/* have: the layers present in each chunk (NULL: all). state: {feature, attempt, rng lo,
 * rng hi} where to start (feature 0, attempt 0, lo = hi = 0: from the start) and, on
 * return, where to resume. spec: speculative (no terrain: every attempt counted as failed,
 * its needs added). Returns the dungeons placed by this call; state[0] == 2 when done,
 * else need[] has the layers missing for attempt state[1]. */
CV_EXPORT int cv_dungeons118b(uint64_t seed, int cx, int cz, uint8_t **ch, const uint64_t *have, uint32_t solid,
                              uint32_t cannot, uint64_t *state, int spec, uint64_t *need, int64_t *out, int cap)
{
    DgW w = {ch, cx - 1, cz - 1, solid, cannot};
    Xoroshiro r;
    static const int IDX[2] = {2, 3}, CNT[2] = {10, 4}, LO[2] = {0, -58}, HI[2] = {319, -1};
    xSetSeed(&r, seed);
    uint64_t a = (uint64_t)wg_long(&r) | 1, b = (uint64_t)wg_long(&r) | 1;
    uint64_t deco = (((uint64_t)(int64_t)(cx << 4) * a) + ((uint64_t)(int64_t)(cz << 4) * b)) ^ seed;
    int f0 = (int)state[0], t0 = (int)state[1], n = 0;
    int64_t rec[15];
    for (int k = 0; k < 9; k++) need[k] = 0;
    for (int f = f0; f < 2; f++) {
        if (f == f0 && (state[2] | state[3])) { r.lo = state[2]; r.hi = state[3]; }
        else xSetSeed(&r, deco + (uint64_t)IDX[f] + 30000);
        for (int t = (f == f0 ? t0 : 0); t < CNT[f]; t++) {
            Xoroshiro r0 = r;
            int x = wg_int(&r, 16) + (cx << 4);
            int z = wg_int(&r, 16) + (cz << 4);
            int y = wg_int(&r, HI[f] - LO[f] + 1) + LO[f];
            Xoroshiro r1 = r;
            int j = wg_int(&r, 2) + 2, o = wg_int(&r, 2) + 2;
            uint64_t nd[9] = {0};
            dg_need(cx, cz, x, y, z, j, o, nd);
            int miss = 0;
            for (int k = 0; k < 9; k++)
                if (nd[k] & ~(have ? have[k] : ~0ULL)) miss = 1;
            if (spec) {
                for (int k = 0; k < 9; k++) need[k] |= nd[k];
                continue;                                  /* counted as failed: r after j, o */
            }
            if (miss) {
                for (int k = 0; k < 9; k++) need[k] = nd[k] & ~have[k];
                state[0] = (uint64_t)f; state[1] = (uint64_t)t; state[2] = r0.lo; state[3] = r0.hi;
                return n;
            }
            r = r1;
            memset(rec, 0, sizeof rec);
            if (dg_place(&w, &r, x, y, z, rec)) {
                if (n < cap) memcpy(out + 15 * n, rec, sizeof rec);
                n++;
            }
        }
    }
    state[0] = 2; state[1] = 0; state[2] = state[3] = 0;
    return n;
}
CV_EXPORT int cv_dungeons118(uint64_t seed, int cx, int cz, uint8_t **ch, uint32_t solid, uint32_t cannot,
                             int64_t *out, int cap)
{
    uint64_t st[4] = {0, 0, 0, 0}, need[9];
    return cv_dungeons118b(seed, cx, cz, ch, NULL, solid, cannot, st, 0, need, out, cap);
}

/* ───────────── (31/10) one 1.18+ mineshaft piece in one chunk (mineshaft118._P / _room / …) ─────────────
 * blk: the chunk [((y + 64) * 16 + z) * 16 + x] (codes of mineshaft118), changed in place;
 * orig: the chunk before any write (OCEAN_FLOOR_WG: highest sturdy + 1), top: 256 cached
 * heights (-32768: not yet). bb: the piece box, facing N S W E NONE = 0..4; flags: 1 webs,
 * 2 rails, 4 two floors, 8 spider already placed, 16 record chest attempts. rng: the
 * WorldgenRandom (Xoroshiro lo, hi), in / out. chests: 4 int64 each (x, y, z, loot seed);
 * tries: 3 ints each. Returns 0 (liquid around: not placed), 1 placed, 3 placed + spider. */
enum { MS_SOLID, MS_AIR, MS_WATER, MS_LAVA, MS_PLANKS, MS_LOG, MS_FENCE, MS_CHAIN, MS_RAIL, MS_COBWEB,
       MS_TORCH, MS_SPAWNER };
enum { MS_N, MS_S, MS_W, MS_E, MS_NONE };
enum { MS_ROOM, MS_CORRIDOR, MS_CROSSING, MS_STAIRS };
typedef struct {
    uint8_t *b; const uint8_t *orig; int16_t *top; int x0, z0;
    const int *bb; int f;
    Xoroshiro r;
    int64_t *ch; int nch, *tr, ntr, cap, rec;
} MsW;
#define MS_STURDY(v) ((v) == MS_SOLID || (v) == MS_PLANKS || (v) == MS_LOG || (v) == MS_SPAWNER)
#define MS_SOLIDR(v) ((v) == MS_SOLID || (v) == MS_PLANKS || (v) == MS_LOG)
#define MS_WOOD(v) ((v) == MS_PLANKS || (v) == MS_LOG || (v) == MS_FENCE || (v) == MS_CHAIN)
#define MS_SUPDOWN(v) (MS_STURDY(v) || (v) == MS_FENCE || (v) == MS_CHAIN)
static inline int ms_in(MsW *w, int x, int y, int z)
{ return x >= w->x0 && x <= w->x0 + 15 && z >= w->z0 && z <= w->z0 + 15 && y >= -64 && y <= 319; }
static inline int ms_get(MsW *w, int x, int y, int z)
{ return ms_in(w, x, y, z) ? w->b[((((y + 64) << 4) | (z - w->z0)) << 4) | (x - w->x0)] : MS_AIR; }
static inline void ms_set(MsW *w, int x, int y, int z, int v)
{ if (ms_in(w, x, y, z)) w->b[((((y + 64) << 4) | (z - w->z0)) << 4) | (x - w->x0)] = (uint8_t)v; }
static int ms_height(MsW *w, int x, int z)
{
    int i = (z - w->z0) * 16 + (x - w->x0);
    if (w->top[i] == -32768) {
        int y = 319;
        while (y >= -64 && !MS_STURDY(w->orig[((((y + 64) << 4) | (z - w->z0)) << 4) | (x - w->x0)])) y--;
        w->top[i] = (int16_t)(y + 1);
    }
    return w->top[i];
}
static inline void ms_wpos(MsW *w, int x, int y, int z, int *X, int *Y, int *Z)
{
    const int *b = w->bb;
    switch (w->f) {
    case MS_N: *X = b[0] + x; *Z = b[5] - z; break;
    case MS_S: *X = b[0] + x; *Z = b[2] + z; break;
    case MS_W: *X = b[3] - z; *Z = b[2] + x; break;
    case MS_E: *X = b[0] + z; *Z = b[2] + x; break;
    default: *X = x; *Z = z;
    }
    *Y = w->f == MS_NONE ? y : y + b[1];
}
static int ms_getb(MsW *w, int x, int y, int z) { int X, Y, Z; ms_wpos(w, x, y, z, &X, &Y, &Z); return ms_get(w, X, Y, Z); }
static void ms_place(MsW *w, int v, int x, int y, int z)
{
    int X, Y, Z; ms_wpos(w, x, y, z, &X, &Y, &Z);
    if (!ms_in(w, X, Y, Z)) return;
    if (MS_WOOD(ms_get(w, X, Y, Z))) return;
    ms_set(w, X, Y, Z, v);
}
static int ms_interior(MsW *w, int x, int y, int z)
{
    int X, Y, Z; ms_wpos(w, x, y + 1, z, &X, &Y, &Z);
    if (!ms_in(w, X, Y, Z)) return 0;
    return Y < ms_height(w, X, Z);
}
static inline double ms_float(Xoroshiro *r) { return (double)wg_next(r, 24) / 16777216.0; }
static void ms_box(MsW *w, int x0, int y0, int z0, int x1, int y1, int z1, int edge, int inner)
{
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++)
            for (int z = z0; z <= z1; z++) {
                int e = y == y0 || y == y1 || x == x0 || x == x1 || z == z0 || z == z1;
                ms_place(w, e ? edge : inner, x, y, z);
            }
}
static void ms_maybe_box(MsW *w, double f, int x0, int y0, int z0, int x1, int y1, int z1, int edge, int inner,
                         int need_interior)
{
    for (int y = y0; y <= y1; y++)
        for (int x = x0; x <= x1; x++)
            for (int z = z0; z <= z1; z++) {
                if (ms_float(&w->r) > f) continue;
                if (need_interior && !ms_interior(w, x, y, z)) continue;
                int e = y == y0 || y == y1 || x == x0 || x == x1 || z == z0 || z == z1;
                ms_place(w, e ? edge : inner, x, y, z);
            }
}
static void ms_maybe_block(MsW *w, double f, int x, int y, int z, int v)
{ if (ms_float(&w->r) < f) ms_place(w, v, x, y, z); }
static int ms_liquid(MsW *w)
{
    const int *b = w->bb;
    int i = b[0] - 1 > w->x0 ? b[0] - 1 : w->x0, j = b[1] - 1 > -64 ? b[1] - 1 : -64;
    int k = b[2] - 1 > w->z0 ? b[2] - 1 : w->z0;
    int l = b[3] + 1 < w->x0 + 15 ? b[3] + 1 : w->x0 + 15, m = b[4] + 1 < 319 ? b[4] + 1 : 319;
    int n = b[5] + 1 < w->z0 + 15 ? b[5] + 1 : w->z0 + 15;
#define LQ(v) ((v) == MS_WATER || (v) == MS_LAVA)
    for (int x = i; x <= l; x++)
        for (int z = k; z <= n; z++)
            if (LQ(ms_get(w, x, j, z)) || LQ(ms_get(w, x, m, z))) return 1;
    for (int x = i; x <= l; x++)
        for (int y = j; y <= m; y++)
            if (LQ(ms_get(w, x, y, k)) || LQ(ms_get(w, x, y, n))) return 1;
    for (int z = k; z <= n; z++)
        for (int y = j; y <= m; y++)
            if (LQ(ms_get(w, i, y, z)) || LQ(ms_get(w, l, y, z))) return 1;
#undef LQ
    return 0;
}
static void ms_room(MsW *w, const int *ents, int nent)
{
    const int *b = w->bb;
    ms_box(w, b[0], b[1] + 1, b[2], b[3], b[1] + 3 < b[4] ? b[1] + 3 : b[4], b[5], MS_AIR, MS_AIR);
    for (int e = 0; e < nent; e++) {
        const int *E = ents + 6 * e;
        ms_box(w, E[0], E[4] - 2, E[2], E[3], E[4], E[5], MS_AIR, MS_AIR);
    }
    int x0 = b[0], y0 = b[1] + 4, z0 = b[2], x1 = b[3], y1 = b[4], z1 = b[5];
    float fw = (float)(double)(x1 - x0 + 1), fh = (float)(double)(y1 - y0 + 1), fd = (float)(double)(z1 - z0 + 1);
    float cxf = (float)((double)x0 + (double)(float)((double)fw / 2.0));
    float czf = (float)((double)z0 + (double)(float)((double)fd / 2.0));
    float hw = (float)((double)fw * 0.5), hd = (float)((double)fd * 0.5), lim = (float)1.05;
    for (int y = y0; y <= y1; y++) {
        float fy = (float)((double)(float)(double)(y - y0) / (double)fh);
        for (int x = x0; x <= x1; x++) {
            float fx = (float)((double)(float)((double)(float)(double)x - (double)cxf) / (double)hw);
            for (int z = z0; z <= z1; z++) {
                float fz = (float)((double)(float)((double)(float)(double)z - (double)czf) / (double)hd);
                float s = (float)((double)(float)((double)(float)((double)fx * fx) + (double)(float)((double)fy * fy))
                                  + (double)(float)((double)fz * fz));
                if (s <= lim) ms_place(w, MS_AIR, x, y, z);
            }
        }
    }
}
static void ms_stairs(MsW *w)
{
    ms_box(w, 0, 5, 0, 2, 7, 1, MS_AIR, MS_AIR);
    ms_box(w, 0, 0, 7, 2, 2, 8, MS_AIR, MS_AIR);
    for (int i = 0; i < 5; i++)
        ms_box(w, 0, 5 - i - (i < 4 ? 1 : 0), 2 + i, 2, 7 - i, 2 + i, MS_AIR, MS_AIR);
}
static void ms_set_planks(MsW *w, int x, int y, int z)
{
    if (!ms_interior(w, x, y, z)) return;
    int X, Y, Z; ms_wpos(w, x, y, z, &X, &Y, &Z);
    if (!MS_STURDY(ms_get(w, X, Y, Z))) ms_set(w, X, Y, Z, MS_PLANKS);
}
static void ms_crossing(MsW *w, int two)
{
    const int *b = w->bb;
    const int A = MS_AIR;
    if (two) {
        ms_box(w, b[0] + 1, b[1], b[2], b[3] - 1, b[1] + 3 - 1, b[5], A, A);
        ms_box(w, b[0], b[1], b[2] + 1, b[3], b[1] + 3 - 1, b[5] - 1, A, A);
        ms_box(w, b[0] + 1, b[4] - 2, b[2], b[3] - 1, b[4], b[5], A, A);
        ms_box(w, b[0], b[4] - 2, b[2] + 1, b[3], b[4], b[5] - 1, A, A);
        ms_box(w, b[0] + 1, b[1] + 3, b[2] + 1, b[3] - 1, b[1] + 3, b[5] - 1, A, A);
    } else {
        ms_box(w, b[0] + 1, b[1], b[2], b[3] - 1, b[4], b[5], A, A);
        ms_box(w, b[0], b[1], b[2] + 1, b[3], b[4], b[5] - 1, A, A);
    }
    int cs[4][2] = {{b[0] + 1, b[2] + 1}, {b[0] + 1, b[5] - 1}, {b[3] - 1, b[2] + 1}, {b[3] - 1, b[5] - 1}};
    int b1 = b[1], b4 = b[4], b0 = b[0], b3 = b[3], b2 = b[2], b5 = b[5];
    for (int i = 0; i < 4; i++)
        if (ms_getb(w, cs[i][0], b4 + 1, cs[i][1]) != MS_AIR)
            ms_box(w, cs[i][0], b1, cs[i][1], cs[i][0], b4, cs[i][1], MS_PLANKS, A);
    for (int x = b0; x <= b3; x++)
        for (int z = b2; z <= b5; z++)
            ms_set_planks(w, x, b1 - 1, z);
}
static void ms_pillar_or_chain(MsW *w, int x, int y, int z)
{
    int X, Y, Z; ms_wpos(w, x, y, z, &X, &Y, &Z);
    if (!ms_in(w, X, Y, Z)) return;
    int i = 1, down = 1, up = 1;
    while (down || up) {
        if (down) {
            int st = ms_get(w, X, Y - i, Z);
            int repl = st == MS_AIR || st == MS_WATER;
            if (!repl && MS_STURDY(st)) {
                for (int yy = Y - i + 1; yy < Y; yy++) ms_set(w, X, yy, Z, MS_LOG);
                return;
            }
            down = i <= 20 && repl && (Y - i) > -64 + 1;
        }
        if (up) {
            int st = ms_get(w, X, Y + i, Z);
            int repl = st == MS_AIR || st == MS_WATER || st == MS_LAVA;
            if (!repl && MS_SUPDOWN(st)) {
                ms_set(w, X, Y + 1, Z, MS_FENCE);
                for (int yy = Y + 2; yy < Y + i; yy++) ms_set(w, X, yy, Z, MS_CHAIN);
                return;
            }
            up = i <= 50 && repl && (Y + i) < 319;
        }
        i++;
    }
}
static void ms_double_support(MsW *w, int x, int y, int z)
{
    if (ms_getb(w, x, y, z) == MS_PLANKS) ms_pillar_or_chain(w, x, y, z);
    if (ms_getb(w, x + 2, y, z) == MS_PLANKS) ms_pillar_or_chain(w, x + 2, y, z);
}
static void ms_support(MsW *w, int x0, int y0, int z, int y1, int x1)
{
    for (int x = x0; x <= x1; x++)
        if (ms_getb(w, x, y1 + 1, z) == MS_AIR) return;
    ms_box(w, x0, y0, z, x0, y1 - 1, z, MS_FENCE, MS_AIR);
    ms_box(w, x1, y0, z, x1, y1 - 1, z, MS_FENCE, MS_AIR);
    if (wg_int(&w->r, 4) == 0) {
        ms_box(w, x0, y1, z, x0, y1, z, MS_PLANKS, MS_AIR);
        ms_box(w, x1, y1, z, x1, y1, z, MS_PLANKS, MS_AIR);
    } else {
        ms_box(w, x0, y1, z, x1, y1, z, MS_PLANKS, MS_AIR);
        ms_maybe_block(w, 0.05, x0 + 1, y1, z - 1, MS_TORCH);
        ms_maybe_block(w, 0.05, x0 + 1, y1, z + 1, MS_TORCH);
    }
}
static int ms_sturdy_nb(MsW *w, int x, int y, int z, int need)
{
    static const int D[6][3] = {{0, -1, 0}, {0, 1, 0}, {0, 0, -1}, {0, 0, 1}, {-1, 0, 0}, {1, 0, 0}};
    int X, Y, Z, n = 0; ms_wpos(w, x, y, z, &X, &Y, &Z);
    for (int d = 0; d < 6; d++) {
        int a = X + D[d][0], b = Y + D[d][1], c = Z + D[d][2];
        if (ms_in(w, a, b, c) && MS_STURDY(ms_get(w, a, b, c)) && ++n >= need) return 1;
    }
    return 0;
}
static void ms_cobweb(MsW *w, double f, int x, int y, int z)
{
    if (ms_interior(w, x, y, z) && ms_float(&w->r) < f && ms_sturdy_nb(w, x, y, z, 2))
        ms_place(w, MS_COBWEB, x, y, z);
}
static void ms_chest(MsW *w, int x, int y, int z)
{
    int X, Y, Z; ms_wpos(w, x, y, z, &X, &Y, &Z);
    if (w->rec && ms_in(w, X, Y, Z)) {
        if (w->ntr < w->cap) { w->tr[3 * w->ntr] = X; w->tr[3 * w->ntr + 1] = Y; w->tr[3 * w->ntr + 2] = Z; }
        w->ntr++;
    }
    if (ms_in(w, X, Y, Z) && ms_get(w, X, Y, Z) == MS_AIR && ms_get(w, X, Y - 1, Z) != MS_AIR) {
        wg_next(&w->r, 1);
        ms_place(w, MS_RAIL, x, y, z);
        int64_t s = wg_long(&w->r);
        if (w->nch < w->cap) {
            int64_t *c = w->ch + 4 * w->nch; c[0] = X; c[1] = Y; c[2] = Z; c[3] = s;
        }
        w->nch++;
    }
}
static int ms_corridor(MsW *w, int length, int flags)
{
    int webs = flags & 1, spider = 0, done = (flags & 8) != 0;
    int m = length * 5 - 1;
    ms_box(w, 0, 0, 0, 2, 1, m, MS_AIR, MS_AIR);
    ms_maybe_box(w, 0.8, 0, 2, 0, 2, 2, m, MS_AIR, MS_AIR, 0);
    if (webs) ms_maybe_box(w, 0.6, 0, 0, 0, 2, 1, m, MS_COBWEB, MS_AIR, 1);
    static const double CF[8] = {0.1, 0.1, 0.1, 0.1, 0.05, 0.05, 0.05, 0.05};
    static const int CX[8] = {0, 2, 0, 2, 0, 2, 0, 2}, CZ[8] = {-1, -1, 1, 1, -2, -2, 2, 2};
    for (int i = 0; i < length; i++) {
        int o = 2 + i * 5;
        ms_support(w, 0, 0, o, 2, 2);
        for (int c = 0; c < 8; c++) ms_cobweb(w, CF[c], CX[c], 2, o + CZ[c]);
        if (wg_int(&w->r, 100) == 0) ms_chest(w, 2, 0, o - 1);
        if (wg_int(&w->r, 100) == 0) ms_chest(w, 0, 0, o + 1);
        if (webs && !done) {
            int q = o - 1 + wg_int(&w->r, 3);
            int X, Y, Z; ms_wpos(w, 1, 0, q, &X, &Y, &Z);
            if (ms_in(w, X, Y, Z) && ms_interior(w, 1, 0, q)) {
                done = spider = 1;
                ms_set(w, X, Y, Z, MS_SPAWNER);
            }
        }
    }
    for (int x = 0; x < 3; x++)
        for (int z = 0; z <= m; z++) ms_set_planks(w, x, -1, z);
    ms_double_support(w, 0, -1, 2);
    if (length > 1) ms_double_support(w, 0, -1, m - 2);
    if (flags & 2)
        for (int z = 0; z <= m; z++) {
            int s = ms_getb(w, 1, -1, z);
            if (s != MS_AIR && MS_SOLIDR(s))
                ms_maybe_block(w, ms_interior(w, 1, 0, z) ? 0.7 : 0.9, 1, 0, z, MS_RAIL);
        }
    return spider;
}
CV_EXPORT int cv_ms118_piece(uint8_t *blk, const uint8_t *orig, int16_t *top, int cx, int cz, int type,
                             const int *bb, int facing, int length, int flags, const int *ents, int nent,
                             uint64_t *rng, int64_t *chests, int *nchest, int *tries, int *ntries, int cap)
{
    MsW w = {blk, orig, top, cx << 4, cz << 4, bb, facing, {rng[0], rng[1]}, chests, 0, tries, 0, cap,
             (flags & 16) != 0};
    if (ms_liquid(&w)) return 0;
    int ret = 1;
    switch (type) {
    case MS_ROOM: ms_room(&w, ents, nent); break;
    case MS_CORRIDOR: if (ms_corridor(&w, length, flags)) ret = 3; break;
    case MS_CROSSING: ms_crossing(&w, (flags & 4) != 0); break;
    default: ms_stairs(&w);
    }
    rng[0] = w.r.lo; rng[1] = w.r.hi;
    *nchest = w.nch; *ntries = w.ntr;
    return ret;
}
