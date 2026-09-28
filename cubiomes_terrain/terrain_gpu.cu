/* terrain_gpu.cu — CUDA port of xpple/cubiomes terrainnoise (block-perfect 1.18+
 * terrain surface Y, noise caves included). FP64 device math = bit-exact vs the CPU.
 *
 * Strategy: the TerrainNoise state (BlendedNoise + 21 cave DoublePerlin + climate
 * noises + 3 splines) is DERIVED ON THE CPU (terrainnoise.c) and uploaded into one
 * contiguous device buffer with the internal pointers fixed up to device addresses.
 * The kernel then only SAMPLES: 1 thread per output cell -> 4 corner density columns
 * -> trilinear generateColumn -> surface Y. Reuses no SSA code (self-contained, built
 * against xpple's cubiomes in cubiomes_terrain/).
 *
 *  ── STEP (a): device noise primitives (this file, growing). ──
 *  next: (b) density functions, (c) column+kernel, (d) host upload+launch.
 */
#include <stdint.h>
#include <math.h>

#define TG_DEV __device__ static inline

/* ───────────────────── GPU noise structs (mirror xpple's) ─────────────────────
 * Pointers (OctaveG.octaves, SplineG.val) are DEVICE addresses, fixed up by the
 * host after the contiguous upload. Layout matches the CPU structs field-for-field. */
typedef struct {
    uint8_t d[256 + 1];
    uint8_t h2;
    double  a, b, c;
    double  amplitude;
    double  lacunarity;
    double  d2;
    double  t2;
} PerlinG;

typedef struct { int octcnt; PerlinG *octaves; } OctaveG;          /* octaves -> device */
typedef struct { double amplitude; OctaveG octA, octB; } DPerlinG;

typedef struct {
    double  xzScale, yScale, xzFactor, yFactor;
    double  xzMultiplier, yMultiplier;
    double  smearScaleMultiplier, smearedYScale, factoredSmearedYScale;
    OctaveG octmin, octmax, octmain;
    PerlinG oct[16 + 16 + 8];
} BlendedG;

/* cubic spline (same shape as cubiomes Spline / FixSpline) */
typedef struct SplineG_s { int len, typ; float loc[12]; float der[12];
                           struct SplineG_s *val[12]; } SplineG;
typedef struct { int len; float val; } FixSplineG;

/* ── FP32 fast path (opt-in: nvcc -DTGPU_FP32) ────────────────────────────────
 * All consumer NVIDIA GPUs run FP64 at ~1/64 of FP32, so the bit-exact FP64
 * density chain is the bottleneck. With -DTGPU_FP32 the *device arithmetic*
 * switches to float (much faster) while the structs above and the host upload
 * stay double (the upload reinterprets the CPU's FP64 noise state). The numeric
 * literals + struct-field casts below are written for this FP32 path, so the
 * non-FP32 build is no longer the bit-exact reference — that role belongs to the
 * CPU lib (terrainnoise.c → terrain.dll), UNTOUCHED, which drives the per-block
 * hover read-out, so the value you READ stays exact.
 * Precision: FP32 loses the fraction of very large coordinates, so the MAP may
 * differ by ~0-1 block past ~1M blocks from origin (same trade-off as chunkbase's
 * WebGPU). Validate with -DTGPU_TEST -DTGPU_FP32 (prints GPU-FP32 vs CPU-FP64). */
#ifdef TGPU_FP32
  #define double float
  #define floor  floorf
  #define fabs   fabsf
  #define fmax   fmaxf
  #define fmin   fminf
#endif

/* ───────────────────────────── math helpers ──────────────────────────────── */
TG_DEV double tg_maintain(double x) {
    return x - floor(x / 3.3554432e7f + 0.5f) * 3.3554432e7f;
}
TG_DEV double tg_lerp(double t, double a, double b) { return a + t * (b - a); }
TG_DEV double tg_clamp(double v, double lo, double hi) { return v < lo ? lo : (v > hi ? hi : v); }
TG_DEV double tg_clampedMap(double v, double i0, double i1, double o0, double o1) {
    double t = (v - i0) / (i1 - i0);
    t = t < 0.0f ? 0.0f : (t > 1.0f ? 1.0f : t);
    return o0 + t * (o1 - o0);
}
TG_DEV double tg_map(double v, double i0, double i1, double o0, double o1) {
    return o0 + (v - i0) * (o1 - o0) / (i1 - i0);
}
TG_DEV double tg_lerp3(double dx, double dy, double dz,
        double v000, double v100, double v010, double v110,
        double v001, double v101, double v011, double v111) {
    double x00 = tg_lerp(dx, v000, v100), x10 = tg_lerp(dx, v010, v110);
    double x01 = tg_lerp(dx, v001, v101), x11 = tg_lerp(dx, v011, v111);
    double y0 = tg_lerp(dy, x00, x10), y1 = tg_lerp(dy, x01, x11);
    return tg_lerp(dz, y0, y1);
}

/* ───────────────────────────── noise sampling ────────────────────────────── */
/* cubiomes indexedLerp gradient (exact cEdge tables) */
TG_DEV double tg_grad(int h, double d1, double d2, double d3) {
    const double cX[16] = { 1.0f,-1.0f, 1.0f,-1.0f, 1.0f,-1.0f, 1.0f,-1.0f,
                            0.0f, 0.0f, 0.0f, 0.0f, 1.0f, 0.0f,-1.0f, 0.0f };
    const double cY[16] = { 1.0f, 1.0f,-1.0f,-1.0f, 0.0f, 0.0f, 0.0f, 0.0f,
                            1.0f,-1.0f, 1.0f,-1.0f, 1.0f,-1.0f, 1.0f,-1.0f };
    const double cZ[16] = { 0.0f, 0.0f, 0.0f, 0.0f, 1.0f, 1.0f,-1.0f,-1.0f,
                            1.0f, 1.0f,-1.0f,-1.0f, 0.0f, 1.0f, 0.0f,-1.0f };
    h &= 0xf;
    return cX[h]*d1 + cY[h]*d2 + cZ[h]*d3;
}
/* faithful FP64 port of xpple samplePerlin (noise.c) */
TG_DEV double tg_samplePerlin(const PerlinG *p, double d1, double d2, double d3,
                              double yamp, double ymax) {
    uint8_t h1, h2, h3; double t1, t2, t3;
    if (d2 == 0.0f) { d2 = (float)p->d2; h2 = p->h2; t2 = (float)p->t2; }
    else { d2 += (float)p->b; double i2 = floor(d2); d2 -= i2; h2 = (uint8_t)(int)i2;
           t2 = d2*d2*d2 * (d2 * (d2*6.0f-15.0f) + 10.0f); }
    d1 += (float)p->a; d3 += (float)p->c;
    double i1 = floor(d1), i3 = floor(d3);
    d1 -= i1; d3 -= i3; h1 = (uint8_t)(int)i1; h3 = (uint8_t)(int)i3;
    t1 = d1*d1*d1 * (d1 * (d1*6.0f-15.0f) + 10.0f);
    t3 = d3*d3*d3 * (d3 * (d3*6.0f-15.0f) + 10.0f);
    if (yamp) { double yc = (ymax >= 0.0f && ymax < d2) ? ymax : d2; d2 -= floor(yc / yamp) * yamp; }
    const uint8_t *idx = p->d;
    uint8_t a1 = idx[h1] + h2,     b1 = idx[h1 + 1] + h2;
    uint8_t a2 = idx[a1] + h3,     b2 = idx[b1] + h3;
    uint8_t a3 = idx[a1 + 1] + h3, b3 = idx[b1 + 1] + h3;
    double l1 = tg_grad(idx[a2],     d1,     d2,     d3);
    double l2 = tg_grad(idx[b2],     d1-1.0f, d2,     d3);
    double l3 = tg_grad(idx[a3],     d1,     d2-1.0f, d3);
    double l4 = tg_grad(idx[b3],     d1-1.0f, d2-1.0f, d3);
    double l5 = tg_grad(idx[a2 + 1], d1,     d2,     d3-1.0f);
    double l6 = tg_grad(idx[b2 + 1], d1-1.0f, d2,     d3-1.0f);
    double l7 = tg_grad(idx[a3 + 1], d1,     d2-1.0f, d3-1.0f);
    double l8 = tg_grad(idx[b3 + 1], d1-1.0f, d2-1.0f, d3-1.0f);
    l1 = tg_lerp(t1, l1, l2); l3 = tg_lerp(t1, l3, l4);
    l5 = tg_lerp(t1, l5, l6); l7 = tg_lerp(t1, l7, l8);
    l1 = tg_lerp(t2, l1, l3); l5 = tg_lerp(t2, l5, l7);
    return tg_lerp(t3, l1, l5);
}

TG_DEV double tg_sampleOctave(const OctaveG *n, double x, double y, double z) {
    double v = 0;
    for (int i = 0; i < n->octcnt; i++) {
        const PerlinG *p = n->octaves + i;
        double lf = (float)p->lacunarity;
        double pv = tg_samplePerlin(p, tg_maintain(x*lf), tg_maintain(y*lf), tg_maintain(z*lf), 0, 0);
        v += (float)p->amplitude * pv;
    }
    return v;
}

TG_DEV double tg_sampleDP(const DPerlinG *n, double x, double y, double z) {
    const double f = 337.0f / 331.0f;
    double v = tg_sampleOctave(&n->octA, x, y, z) + tg_sampleOctave(&n->octB, x*f, y*f, z*f);
    return v * (float)n->amplitude;
}

/* faithful FP64 port of sampleBase3dNoise (BlendedNoise, biomenoise.c) */
TG_DEV double tg_sampleBase3d(const BlendedG *bn, int x, int y, int z) {
    double sX = x * (float)bn->xzMultiplier, sY = y * (float)bn->yMultiplier, sZ = z * (float)bn->xzMultiplier;
    double fX = sX / (float)bn->xzFactor, fY = sY / (float)bn->yFactor, fZ = sZ / (float)bn->xzFactor;
    double mainT = 0.0f, o = 1.0f;
    for (int i = 0; i < 8; i++) {
        mainT += tg_samplePerlin(&bn->octmain.octaves[i], tg_maintain(fX*o), tg_maintain(fY*o),
                    tg_maintain(fZ*o), (float)bn->factoredSmearedYScale*o, fY*o) / o;
        o /= 2.0f;
    }
    double q = (mainT / 10.0f + 1.0f) / 2.0f;
    double minT = 0.0f, maxT = 0.0f; o = 1.0f;
    for (int i = 0; i < 16; i++) {
        double yA = (float)bn->smearedYScale*o, yM = sY*o;
        double aX = tg_maintain(sX*o), aY = tg_maintain(sY*o), aZ = tg_maintain(sZ*o);
        if (q < 1.0f) minT += tg_samplePerlin(&bn->octmin.octaves[i], aX, aY, aZ, yA, yM) / o;
        if (q > 0.0f) maxT += tg_samplePerlin(&bn->octmax.octaves[i], aX, aY, aZ, yA, yM) / o;
        o /= 2.0f;
    }
    return tg_lerp(tg_clamp(q, 0.0f, 1.0f), minT/512.0f, maxT/512.0f) / 128.0f;
}

/* cubic spline eval — ITERATIVE (explicit stack). Recursion on the device (even
 * __noinline__) faults here — the ABI recursion doesn't survive the 63 KB kernel
 * frame. This is bit-identical to the recursive form (verified in host C). Depth is
 * tiny (~6), stk[16] is ample. */
__device__ float tg_getSpline(const SplineG *root, const float *vals) {
    struct { const SplineG *sp; int st; float a,b,c,n; const SplineG *snd; } stk[16];
    int top = 0;
    stk[0].sp = root; stk[0].st = 0;
    float ret = 0.0f;
    while (top >= 0) {
        const SplineG *sp = stk[top].sp;
        int st = stk[top].st;
        if (st == 0) {
            if (sp->len == 1) { ret = ((const FixSplineG*)sp)->val; top--; continue; }
            float f = vals[sp->typ]; int i;
            for (i = 0; i < sp->len; i++) if (sp->loc[i] >= f) break;
            if (i == 0 || i == sp->len) {
                if (i) i--;
                stk[top].a = sp->der[i] * (f - sp->loc[i]);      /* add after child */
                stk[top].st = 3;
                stk[top+1].sp = sp->val[i]; stk[top+1].st = 0; top++;
            } else {
                float g = sp->loc[i-1], h = sp->loc[i], k = (f - g) / (h - g);
                stk[top].a = k; stk[top].b = sp->der[i-1]*(h-g); stk[top].c = sp->der[i]*(h-g);
                stk[top].snd = sp->val[i]; stk[top].st = 1;
                stk[top+1].sp = sp->val[i-1]; stk[top+1].st = 0; top++;
            }
        } else if (st == 3) {                                     /* edge: child + a */
            ret = ret + stk[top].a; top--;
        } else if (st == 1) {                                     /* got first child */
            stk[top].n = ret; stk[top].st = 2;
            stk[top+1].sp = stk[top].snd; stk[top+1].st = 0; top++;
        } else {                                                  /* st==2: got second */
            float n = stk[top].n, o = ret, k = stk[top].a;
            float p = stk[top].b - (o - n), q = -stk[top].c + (o - n);
            ret = (n + k*(o - n)) + k*(1.0f - k)*(p + k*(q - p));
            top--;
        }
    }
    return ret;
}

/* ───────────────────── GPU TerrainNoise params (uploaded) ─────────────────── */
enum { TGO_PILLAR, TGO_PILLAR_RARENESS, TGO_PILLAR_THICKNESS, TGO_SPAGHETTI_2D,
    TGO_SPAGHETTI_2D_ELEVATION, TGO_SPAGHETTI_2D_MODULATOR, TGO_SPAGHETTI_2D_THICKNESS,
    TGO_SPAGHETTI_3D_1, TGO_SPAGHETTI_3D_2, TGO_SPAGHETTI_3D_RARITY, TGO_SPAGHETTI_3D_THICKNESS,
    TGO_SPAGHETTI_ROUGHNESS, TGO_SPAGHETTI_ROUGHNESS_MODULATOR, TGO_CAVE_ENTRANCE, TGO_CAVE_LAYER,
    TGO_CAVE_CHEESE, TGO_NOODLE, TGO_NOODLE_THICKNESS, TGO_NOODLE_RIDGE_A, TGO_NOODLE_RIDGE_B,
    TGO_JAGGED, TGO_NUM };
enum { TG_NP_TEMP, TG_NP_HUMID, TG_NP_CONT, TG_NP_EROS, TG_NP_SHIFT, TG_NP_WEIRD, TG_NP_NUM };

typedef struct {
    BlendedG base3dNoise;
    DPerlinG noises[TGO_NUM];     /* 21 cave noises */
    DPerlinG climate[TG_NP_NUM];  /* cont/eros/weird/shift… */
    SplineG *offsetSpline;        /* g.bn.sp */
    SplineG *factorSpline;
    SplineG *jaggednessSpline;
} TerrainNoiseG;

/* ───────────────────────────── density helpers ───────────────────────────── */
TG_DEV int    tg_floordiv(int a, int b) { int q=a/b; if((a%b)&&((a<0)!=(b<0)))q--; return q; }
TG_DEV double tg_pav(double w) { return -(fabs(fabs(w)-0.6666667f)-0.33333334f)*3.0f; }
TG_DEV double tg_mapFromUnit(double in, double f, double t) { return (f+t)*0.5f + ((t-f)*0.5f)*in; }
TG_DEV double tg_remap(double v, double iMin, double iMax, double oMin, double oMax) {
    double s=(oMax-oMin)/(iMax-iMin); return v*s + (oMin - iMin*s);
}
TG_DEV double tg_rangeChoice(double in, double lo, double hi, double yes, double no) {
    return (in>=lo && in<hi) ? yes : no;
}
TG_DEV double tg_yLimited(double in, double yes, int minY, int maxY, double no) {
    return tg_rangeChoice(in, minY, maxY+1, yes, no);
}
TG_DEV double tg_noiseGradDensity(double mn, double mx) { double f=mx*mn; return f>0.?f*4.:f; }
TG_DEV double tg_postProcess(double d) { double c=tg_clamp(d*0.64f,-1.0f,1.0f); return c/2.0f - c*c*c/24.0f; }
TG_DEV double tg_slide(double in, int minY, int height, int tS, int tE, double tD,
                       int bS, int bE, double bD, int y) {
    double a = tg_clampedMap(y, minY+height-tS, minY+height-tE, 1.0f, 0.0f);
    double b = tg_lerp(a, tD, in);
    double c = tg_clampedMap(y, minY+bS, minY+bE, 0.0f, 1.0f);
    return tg_lerp(c, bD, b);
}
TG_DEV double tg_slideOW(double d, int y) { return tg_slide(d,-64,384,80,64,-0.078125f,0,24,0.1171875f,y); }
TG_DEV double tg_rar2d(double v) { return v<-0.75f?0.5f:(v<-0.5f?0.75f:(v<0.5f?1.0f:(v<0.75f?2.0f:3.0f))); }
TG_DEV double tg_rar3d(double v) { return v<-0.5f?0.75f:(v<0.0f?1.0f:(v<0.5f?1.5f:2.0f)); }

/* ───────────────────────── density functions (port) ──────────────────────── */
#define DP(i) (&P->noises[i])
TG_DEV double tg_spaghettiRoughness(const TerrainNoiseG *P, int x, int y, int z) {
    double m = tg_map(tg_sampleDP(DP(TGO_SPAGHETTI_ROUGHNESS_MODULATOR), x,y,z), -1.0f,1.0f, 0.0f,0.1f);
    return (0.4f - fabs(tg_sampleDP(DP(TGO_SPAGHETTI_ROUGHNESS), x,y,z))) * m;
}
TG_DEV double tg_spaghetti2d(const TerrainNoiseG *P, int x, int y, int z) {
    double mod = tg_sampleDP(DP(TGO_SPAGHETTI_2D_MODULATOR), x*2.0f,y,z*2.0f);
    double rar = tg_rar2d(mod);
    double th  = tg_map(tg_sampleDP(DP(TGO_SPAGHETTI_2D_THICKNESS), x*2.0f,y,z*2.0f), -1.0f,1.0f, 0.6f,1.3f);
    double s2  = tg_sampleDP(DP(TGO_SPAGHETTI_2D), x/rar,y/rar,z/rar);
    double a   = fabs(rar*s2) - 0.083f*th;
    double el  = tg_map(tg_sampleDP(DP(TGO_SPAGHETTI_2D_ELEVATION), x,0.0f,z), -1.0f,1.0f, tg_floordiv(-64,8), 8.0f);
    double b   = fabs(el - y/8.0f) - th;
    return tg_clamp(fmax(b*b*b, a), -1.0f, 1.0f);
}
TG_DEV double tg_spaghetti3d(const TerrainNoiseG *P, int x, int y, int z) {
    double rr = tg_sampleDP(DP(TGO_SPAGHETTI_3D_RARITY), x*2.0f,y,z*2.0f);
    double th = tg_mapFromUnit(tg_sampleDP(DP(TGO_SPAGHETTI_3D_THICKNESS), x,y,z), -0.065f, -0.088f);
    double rar = tg_rar3d(rr);
    double s1 = rar*fabs(tg_sampleDP(DP(TGO_SPAGHETTI_3D_1), x/rar,y/rar,z/rar));
    double s2 = rar*fabs(tg_sampleDP(DP(TGO_SPAGHETTI_3D_2), x/rar,y/rar,z/rar));
    return tg_clamp(fmax(s1,s2)+th, -1.0f, 1.0f);
}
TG_DEV double tg_caveEntrance(const TerrainNoiseG *P, int x, int y, int z) {
    double c = tg_sampleDP(DP(TGO_CAVE_ENTRANCE), x*0.75f,y*0.5f,z*0.75f);
    return c + 0.37f + tg_clampedMap(y, -10,30, 0.3f,0.0f);
}
TG_DEV double tg_entrances(const TerrainNoiseG *P, int x, int y, int z, double rough) {
    return fmin(tg_caveEntrance(P,x,y,z), rough + tg_spaghetti3d(P,x,y,z));
}
TG_DEV double tg_caveLayer(const TerrainNoiseG *P, int x, int y, int z) {
    double c = tg_sampleDP(DP(TGO_CAVE_LAYER), x, y*8.0f, z); return c*c*4.0f;
}
TG_DEV double tg_slopedCheese(const TerrainNoiseG *P, int x, int y, int z,
                              double depth, double factor, double jagged) {
    depth = depth + tg_clampedMap(y, -64,320, 1.5f,-1.5f);
    double d = tg_noiseGradDensity(factor, depth + jagged);
    return d + tg_sampleBase3d(&P->base3dNoise, x, y, z);
}
TG_DEV double tg_caveCheese(const TerrainNoiseG *P, int x, int y, int z, double sloped) {
    double c = tg_sampleDP(DP(TGO_CAVE_CHEESE), x, y*0.6666666666666666f, z);
    return tg_clamp(0.27f+c, -1.0f,1.0f) + tg_clamp(1.5f + (-0.64f*sloped), 0.0f,0.5f);
}
TG_DEV double tg_pillars(const TerrainNoiseG *P, int x, int y, int z) {
    double p  = tg_sampleDP(DP(TGO_PILLAR), x*25.0f,y*0.3f,z*25.0f);
    double pr = tg_map(tg_sampleDP(DP(TGO_PILLAR_RARENESS), x,y,z), -1.0f,1.0f, 0.0f,-2.0f);
    double pt = tg_map(tg_sampleDP(DP(TGO_PILLAR_THICKNESS), x,y,z), -1.0f,1.0f, 0.0f,1.1f);
    return (p*2.0f + pr) * pt*pt*pt;
}
TG_DEV double tg_noodle(const TerrainNoiseG *P, int x, int y, int z) {
    double n  = tg_yLimited(y, tg_sampleDP(DP(TGO_NOODLE), x,y,z), -60,320, -1);
    double nt = tg_yLimited(y, tg_map(tg_sampleDP(DP(TGO_NOODLE_THICKNESS), x,y,z), -1.0f,1.0f, -0.05f,-0.1f), -60,320, 0);
    double k = 2.6666666666666665f;
    double ra = tg_yLimited(y, tg_sampleDP(DP(TGO_NOODLE_RIDGE_A), x*k,y*k,z*k), -60,320, 0);
    double rb = tg_yLimited(y, tg_sampleDP(DP(TGO_NOODLE_RIDGE_B), x*k,y*k,z*k), -60,320, 0);
    double sm = 1.5f * fmax(fabs(ra), fabs(rb));
    return tg_rangeChoice(n, -1000000.0f, 0.0f, 64.0f, nt + sm);
}
TG_DEV double tg_underground(const TerrainNoiseG *P, int x, int y, int z,
                             double rough, double entr, double sloped) {
    double s2 = tg_spaghetti2d(P,x,y,z);
    double cl = tg_caveLayer(P,x,y,z);
    double a  = fmin(cl + tg_caveCheese(P,x,y,z,sloped), entr);
    double b  = fmin(a, s2 + rough);
    double pi = tg_pillars(P,x,y,z);
    double c  = (pi>=-1000000.0f && pi<0.03f) ? -1000000.0f : pi;
    return fmax(b, c);
}
TG_DEV double tg_finalDensity(const TerrainNoiseG *P, int x, int y, int z,
                              double rough, double entr, double sloped) {
    double a = fmin(sloped, 5.0f*entr);
    double b = tg_rangeChoice(sloped, -1000000.0f, 1.5625f, a, tg_underground(P,x,y,z,rough,entr,sloped));
    return fmin(tg_postProcess(tg_slideOW(b, y)), tg_noodle(P,x,y,z));
}
#undef DP

/* ─────────────────── (c) climate params + column + surface ─────────────────── */
TG_DEV void tg_noiseParams(const TerrainNoiseG *P, int cellX, int cellZ, float np[4]) {
    double px = cellX + tg_sampleDP(&P->climate[TG_NP_SHIFT], cellX, 0, cellZ) * 4.0f;
    double pz = cellZ + tg_sampleDP(&P->climate[TG_NP_SHIFT], cellZ, cellX, 0) * 4.0f;
    np[0] = (float)tg_sampleDP(&P->climate[TG_NP_CONT],  px, 0, pz);
    np[1] = (float)tg_sampleDP(&P->climate[TG_NP_EROS],  px, 0, pz);
    float w = (float)tg_sampleDP(&P->climate[TG_NP_WEIRD], px, 0, pz);
    np[2] = (float)tg_pav(w);
    np[3] = w;
}
/* one noise column of 48+1 density values (cellHeight = 8) */
TG_DEV void tg_noiseColumn(const TerrainNoiseG *P, int cellX, int cellZ, double buf[49]) {
    int x = cellX * 4, z = cellZ * 4;
    float np[4]; tg_noiseParams(P, cellX, cellZ, np);
    double depth  = tg_getSpline(P->offsetSpline, np) - 0.50375f;
    double factor = tg_getSpline(P->factorSpline, np);
    double jagged = tg_sampleDP(&P->noises[TGO_JAGGED], x*1500.0f, 0, z*1500.0f);
    jagged = jagged >= 0.0f ? jagged : jagged / 2.0f;
    jagged *= tg_getSpline(P->jaggednessSpline, np);
    for (int i = 0; i < 49; i++) {
        int y = -64 + i * 8;
        double rough  = tg_spaghettiRoughness(P, x, y, z);
        double entr   = tg_entrances(P, x, y, z, rough);
        double sloped = tg_slopedCheese(P, x, y, z, depth, factor, jagged);
        buf[i] = tg_finalDensity(P, x, y, z, rough, entr, sloped);
    }
}
/* trilinear-interpolate 4 corner columns -> world Y of air above the highest solid */
TG_DEV int tg_surfaceFromColumns(int x, int z, const double d00[49], const double d01[49],
                                 const double d10[49], const double d11[49]) {
    const double pXZ[4] = {0.0f, 0.25f, 0.5f, 0.75f};
    const double pY[8]  = {0.0f, 0.125f, 0.25f, 0.375f, 0.5f, 0.625f, 0.75f, 0.875f};
    double pcX = pXZ[x & 3], pcZ = pXZ[z & 3];
    for (int cellY = 47; cellY >= 0; cellY--) {
        double n000=d00[cellY],   n001=d01[cellY],   n100=d10[cellY],   n101=d11[cellY];
        double n010=d00[cellY+1], n011=d01[cellY+1], n110=d10[cellY+1], n111=d11[cellY+1];
        for (int relY = 7; relY >= 0; relY--) {
            double noise = tg_lerp3(pcX, pY[relY], pcZ, n000,n100,n010,n110, n001,n101,n011,n111);
            int y = cellY * 8 + relY;
            if (noise > 0.0f) return y + 1;
        }
    }
    return -64;
}
/* full surface Y at block (x,z): build the 4 corner columns + interpolate. */
TG_DEV int tg_surfaceY(const TerrainNoiseG *P, int x, int z) {
    int cx = x >> 2, cz = z >> 2;
    double d00[49], d01[49], d10[49], d11[49];
    tg_noiseColumn(P, cx,     cz,     d00);
    tg_noiseColumn(P, cx,     cz + 1, d01);
    tg_noiseColumn(P, cx + 1, cz,     d10);
    tg_noiseColumn(P, cx + 1, cz + 1, d11);
    return tg_surfaceFromColumns(x, z, d00, d01, d10, d11);
}
/* ───────────────────── (d) host upload + CUDA kernel ───────────────────────
 * Design: the cheap per-cell terrain shaper (depth/factor/jagged = climate +
 * splines) is computed on the CPU and uploaded as a small grid; the GPU only does
 * the expensive 3D density (blended noise + 21 cave noises) → column → surface Y.
 * This avoids flattening the spline pointer-trees for the device. */
#ifdef __CUDACC__
/* The CPU headers below declare DOUBLE-precision functions (sampleNoiseColumn,
 * generateColumn, printf…). Turn the FP32 macro OFF while including them so their
 * `double` stays double, then turn it back ON for the device kernel/columns that
 * run the float device maths. */
#ifdef TGPU_FP32
  #undef double
  #undef floor
  #undef fabs
  #undef fmax
  #undef fmin
#endif
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stddef.h>
#include "terrainnoise.h"     /* CPU: derive state + per-cell shaper */
#include <thread>             /* parallelise the per-cell shaper loop (≤12 threads) */
#include <chrono>             /* dfj-vs-kernel timing breakdown */
#ifdef TGPU_FP32
  #define double float
  #define floor  floorf
  #define fabs   fabsf
  #define fmax   fmaxf
  #define fmin   fminf
#endif

#ifdef _WIN32
  #define TGPU_EXPORT extern "C" __declspec(dllexport)
#else
  #define TGPU_EXPORT extern "C"
#endif

/* one column from PRECOMPUTED depth/factor/jagged (splines already done, CPU-side) */
__device__ static void tg_columnDFJ(const TerrainNoiseG *P, int cellX, int cellZ,
                                    double depth, double factor, double jagged, double buf[49]) {
    int x = cellX * 4, z = cellZ * 4;
    for (int i = 0; i < 49; i++) {
        int y = -64 + i * 8;
        double rough  = tg_spaghettiRoughness(P, x, y, z);
        double entr   = tg_entrances(P, x, y, z, rough);
        double sloped = tg_slopedCheese(P, x, y, z, depth, factor, jagged);
        buf[i] = tg_finalDensity(P, x, y, z, rough, entr, sloped);
    }
}

/* one density value at (x,y,z) from the precomputed per-cell shaper.
 * caves=1 -> full exact density; caves=0 -> terrain shape only (slopedCheese with
 * the same slide+post-process, no entrances/underground/noodle carving). */
__device__ static double tg_densityAt(const TerrainNoiseG *P, int x, int y, int z,
                                      double depth, double factor, double jagged, int caves) {
    double sloped = tg_slopedCheese(P, x, y, z, depth, factor, jagged);
    if (!caves) return tg_postProcess(tg_slideOW(sloped, y));
    double rough  = tg_spaghettiRoughness(P, x, y, z);
    double entr   = tg_entrances(P, x, y, z, rough);
    return tg_finalDensity(P, x, y, z, rough, entr, sloped);
}

/* DESCENDING surface finder — the fast replacement for "build 4 full columns then
 * search". Computes density top→down one cell-row at a time, keeping only the TWO
 * live layers (8 floats, fits in registers → NO local-memory spill, which was the
 * 3-sec bottleneck) and RETURNS at the first solid block (early exit). Produces the
 * exact same value as tg_surfaceFromColumns(full columns): same densities, same
 * trilinear blend, same top-down scan. Returns index-space Y (caller subtracts 64).
 * Corner order: 00=(cx,cz) 01=(cx,cz+1) 10=(cx+1,cz) 11=(cx+1,cz+1). */
__device__ static int tg_surfaceDescend(const TerrainNoiseG *P, int X, int Z, int cx, int cz,
        double dep00,double fac00,double jag00, double dep01,double fac01,double jag01,
        double dep10,double fac10,double jag10, double dep11,double fac11,double jag11, int caves) {
    const double pXZ[4] = {0.0f,0.25f,0.5f,0.75f};
    const double pY[8]  = {0.0f,0.125f,0.25f,0.375f,0.5f,0.625f,0.75f,0.875f};
    double pcX = pXZ[X & 3], pcZ = pXZ[Z & 3];
    int x0c = cx*4, x1c = (cx+1)*4, z0c = cz*4, z1c = (cz+1)*4;
    /* top boundary: buf index i=48  ->  world y = 48*8 - 64 = 320 (build limit). */
    int yTop = 48*8 - 64;
    double t00 = tg_densityAt(P, x0c, yTop, z0c, dep00,fac00,jag00, caves);
    double t01 = tg_densityAt(P, x0c, yTop, z1c, dep01,fac01,jag01, caves);
    double t10 = tg_densityAt(P, x1c, yTop, z0c, dep10,fac10,jag10, caves);
    double t11 = tg_densityAt(P, x1c, yTop, z1c, dep11,fac11,jag11, caves);
    for (int cellY = 47; cellY >= 0; cellY--) {
        int yBot = cellY*8 - 64;
        double b00 = tg_densityAt(P, x0c, yBot, z0c, dep00,fac00,jag00, caves);
        double b01 = tg_densityAt(P, x0c, yBot, z1c, dep01,fac01,jag01, caves);
        double b10 = tg_densityAt(P, x1c, yBot, z0c, dep10,fac10,jag10, caves);
        double b11 = tg_densityAt(P, x1c, yBot, z1c, dep11,fac11,jag11, caves);
        for (int relY = 7; relY >= 0; relY--) {
            /* n000=botC00 n100=botC10 n010=topC00 n110=topC10
               n001=botC01 n101=botC11 n011=topC01 n111=topC11 */
            double noise = tg_lerp3(pcX, pY[relY], pcZ,
                b00, b10, t00, t10,   b01, b11, t01, t11);
            int y = cellY*8 + relY;
            if (noise > 0.0f) return y + 1;
        }
        t00=b00; t01=b01; t10=b10; t11=b11;     /* this bottom is next cell's top */
    }
    return -64;                                  /* nothing solid (sentinel) */
}

/* 1 thread / output point. dfj grids cover cells [cMinX..][cMinZ..] (cw × ch). */
__global__ void k_surface(const TerrainNoiseG *P,
        const double *depthG, const double *factorG, const double *jaggedG,
        int cMinX, int cMinZ, int cw,
        int x0, int z0, int w, int h, int step, int caves, int *out) {
    int idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= w * h) return;
    int i = idx % w, j = idx / w;
    int X = x0 + i * step, Z = z0 + j * step;
    int cx = X >> 2, cz = Z >> 2;
    int g00 = (cx-cMinX)   + (cz-cMinZ)  *cw;
    int g01 = (cx-cMinX)   + (cz+1-cMinZ)*cw;
    int g10 = (cx+1-cMinX) + (cz-cMinZ)  *cw;
    int g11 = (cx+1-cMinX) + (cz+1-cMinZ)*cw;
    out[idx] = tg_surfaceDescend(P, X, Z, cx, cz,
        depthG[g00],factorG[g00],jaggedG[g00], depthG[g01],factorG[g01],jaggedG[g01],
        depthG[g10],factorG[g10],jaggedG[g10], depthG[g11],factorG[g11],jaggedG[g11], caves) - 64;
}

/* ── end of device arithmetic: restore `double` for the host upload below, which
 * memcpy's the CPU's FP64 noise structs verbatim (must stay 8-byte double). The
 * dfj grid uploaded to k_surface is sized with TGGRID to match the kernel. ── */
#ifdef TGPU_FP32
  #undef double
  #undef floor
  #undef fabs
  #undef fmax
  #undef fmin
  #define TGGRID float
#else
  #define TGGRID double
#endif

/* per-seed cached GPU state */
static int      g_ok = 0;  static uint64_t g_seed = ~0ULL;  static int g_mc = 0;
static TerrainNoiseG *d_P = NULL;  static PerlinG *d_oct = NULL;
static TerrainNoise   g_tn;

static int tgpu_upload(int mc, uint64_t seed) {
    if (g_ok && g_seed == seed && g_mc == mc) return 1;
    /* setupTerrainNoise is seed-INDEPENDENT (mc only) and sets up internal state;
     * calling it again on an already-initialised g_tn corrupted it -> access
     * violation on the 2nd seed of a search (the map never hit this: one seed).
     * Set up ONCE per mc; only RE-SEED (initTerrainNoise) on each new seed. */
    if (!g_ok || g_mc != mc) {
        /* BUG corrigé (26/09/2026) : au CHANGEMENT de version (1.18 → 1.21) dans le
         * même processus, setupTerrainNoise était rappelé sur un g_tn déjà rempli
         * → violation d'accès (reproduit sur CPU : createFixSpline écrit hors
         * limites). Il faut repartir d'une structure vierge. */
        g_ok = 0;
        memset(&g_tn, 0, sizeof(g_tn));
        if (!setupTerrainNoise(&g_tn, mc, 0)) return 0;
    }
    if (!initTerrainNoise(&g_tn, seed, DIM_OVERWORLD)) return 0;
    if (!d_P)   cudaMalloc(&d_P,   sizeof(TerrainNoiseG));
    if (!d_oct) cudaMalloc(&d_oct, sizeof(g_tn.oct));        /* PerlinNoise[2*45] == PerlinG[90] */
    cudaMemcpy(d_oct, g_tn.oct, sizeof(g_tn.oct), cudaMemcpyHostToDevice);
    TerrainNoiseG hP;
    memcpy(&hP.base3dNoise, &g_tn.base3dNoise, sizeof(hP.base3dNoise));
    char *gbo = (char*)d_P + offsetof(TerrainNoiseG, base3dNoise) + offsetof(BlendedG, oct);
    hP.base3dNoise.octmin.octaves  = (PerlinG*)gbo + (g_tn.base3dNoise.octmin.octaves  - g_tn.base3dNoise.oct);
    hP.base3dNoise.octmax.octaves  = (PerlinG*)gbo + (g_tn.base3dNoise.octmax.octaves  - g_tn.base3dNoise.oct);
    hP.base3dNoise.octmain.octaves = (PerlinG*)gbo + (g_tn.base3dNoise.octmain.octaves - g_tn.base3dNoise.oct);
    for (int i = 0; i < TGO_NUM; i++) {
        memcpy(&hP.noises[i], &g_tn.noises[i], sizeof(hP.noises[i]));
        hP.noises[i].octA.octaves = d_oct + (g_tn.noises[i].octA.octaves - g_tn.oct);
        hP.noises[i].octB.octaves = d_oct + (g_tn.noises[i].octB.octaves - g_tn.oct);
    }
    hP.offsetSpline = hP.factorSpline = hP.jaggednessSpline = NULL;  /* unused on device */
    cudaMemcpy(d_P, &hP, sizeof(hP), cudaMemcpyHostToDevice);
    g_ok = 1; g_seed = seed; g_mc = mc;
    return 1;
}

TGPU_EXPORT int tgpu_surface_grid(int mc, uint64_t seed, int x0, int z0,
                                  int w, int h, int step, int caves, int *out) {
    if (!tgpu_upload(mc, seed)) return -1;
    int xmax = x0 + (w-1)*step, zmax = z0 + (h-1)*step;
    int cMinX = x0 >> 2, cMaxX = (xmax >> 2) + 1;
    int cMinZ = z0 >> 2, cMaxZ = (zmax >> 2) + 1;
    int cw = cMaxX - cMinX + 1, ch = cMaxZ - cMinZ + 1;
    size_t ncell = (size_t)cw * ch;
    size_t gsz = sizeof(TGGRID);
    TGGRID *dep = (TGGRID*)malloc(ncell*gsz), *fac = (TGGRID*)malloc(ncell*gsz), *jag = (TGGRID*)malloc(ncell*gsz);

    /* per-cell shaper (depth/factor/jagged): climate noise + splines, FP64 on the
     * CPU. THE hidden cost — it was a SERIAL loop over the whole cell bbox on ONE
     * core, every call (so the GPU sat idle waiting, and only one CPU core was busy:
     * both looked ~idle). Parallelise across up to 12 threads (CPU-lifespan cap);
     * reads of g_tn are const → thread-safe. */
    auto _dfj_rows = [&](int j0, int j1){
        for (int j = j0; j < j1; j++) for (int i = 0; i < cw; i++) {
            int ccx = cMinX + i, ccz = cMinZ + j;
            float np[4]; sampleNoiseParameters(&g_tn.g.bn, ccx, ccz, np);
            double d = getSpline(g_tn.g.bn.sp, np) - 0.50375;
            double f = getSpline(g_tn.factorSpline, np);
            double jj = sampleDoublePerlin(&g_tn.noises[OTP_JAGGED], ccx*4*1500.0, 0, ccz*4*1500.0);
            jj = jj >= 0.0 ? jj : jj/2.0; jj *= getSpline(g_tn.jaggednessSpline, np);
            size_t gi = (size_t)i + (size_t)j*cw; dep[gi]=(TGGRID)d; fac[gi]=(TGGRID)f; jag[gi]=(TGGRID)jj;
        }
    };
    auto _t0 = std::chrono::high_resolution_clock::now();
    int nthreads = (ch < 12) ? ch : 12;          /* cap at 12 (CPU lifespan) */
    if (nthreads < 1) nthreads = 1;
    if (nthreads == 1) {
        _dfj_rows(0, ch);
    } else {
        std::thread pool[12];
        int per = (ch + nthreads - 1) / nthreads, tc = 0;
        for (int t = 0; t < nthreads; t++) {
            int j0 = t*per, j1 = j0+per; if (j1 > ch) j1 = ch;
            if (j0 >= j1) break;
            pool[tc++] = std::thread(_dfj_rows, j0, j1);
        }
        for (int t = 0; t < tc; t++) pool[t].join();
    }
    auto _t1 = std::chrono::high_resolution_clock::now();

    TGGRID *dD,*dF,*dJ; int *dO;
    cudaMalloc(&dD,ncell*gsz); cudaMalloc(&dF,ncell*gsz); cudaMalloc(&dJ,ncell*gsz);
    cudaMalloc(&dO,(size_t)w*h*sizeof(int));
    cudaMemcpy(dD,dep,ncell*gsz,cudaMemcpyHostToDevice);
    cudaMemcpy(dF,fac,ncell*gsz,cudaMemcpyHostToDevice);
    cudaMemcpy(dJ,jag,ncell*gsz,cudaMemcpyHostToDevice);
    int n = w*h, TPB = 128;
    k_surface<<<(n+TPB-1)/TPB, TPB>>>(d_P, dD, dF, dJ, cMinX, cMinZ, cw, x0, z0, w, h, step, caves, dO);
    cudaError_t e = cudaDeviceSynchronize();
    cudaMemcpy(out, dO, (size_t)w*h*sizeof(int), cudaMemcpyDeviceToHost);
    auto _t2 = std::chrono::high_resolution_clock::now();
    cudaFree(dD); cudaFree(dF); cudaFree(dJ); cudaFree(dO); free(dep); free(fac); free(jag);
    {   /* breakdown: dfj = CPU shaper loop, kernel = real GPU work (+ malloc/copy) */
        double dfj_ms = std::chrono::duration<double,std::milli>(_t1-_t0).count();
        double ker_ms = std::chrono::duration<double,std::milli>(_t2-_t1).count();
        printf("[tgpu] cells=%zu (%dx%d) pts=%d caves=%d  dfj_cpu=%.0fms  gpu_kernel+io=%.0fms  thr=%d\n",
               ncell, cw, ch, w*h, caves, dfj_ms, ker_ms, nthreads);
        fflush(stdout);
    }
    return e == cudaSuccess ? 0 : -2;
}

/* ══════════════════════════════════════════════════════════════════════════════
 * GPU RE-SEED (step 1 of the GPU Exact-Y search): build the terrain noise state on
 * the device, per thread, instead of re-seeding on the CPU and uploading per seed.
 * This increment ports + VALIDATES the BLENDED 3D noise re-seed (40 octaves). The
 * sampler (tg_*) is already on-device + FP64 bit-exact, so once the re-seed matches
 * the CPU bit-for-bit, a full per-thread Exact-Y kernel is just reseed → tg_surface.
 * ════════════════════════════════════════════════════════════════════════════ */
#ifndef TGPU_FP32   /* the re-seed must be FP64 (a/b/c offsets) to be bit-exact */
struct XoD { uint64_t lo, hi; };
__device__ __forceinline__ uint64_t xo_rotl(uint64_t x, int b){ return (x<<b)|(x>>(64-b)); }
__device__ __forceinline__ void xo_set(XoD *xr, uint64_t value){
    const uint64_t XL=0x9e3779b97f4a7c15ULL,XH=0x6a09e667f3bcc909ULL;
    const uint64_t A=0xbf58476d1ce4e5b9ULL,B=0x94d049bb133111ebULL;
    uint64_t l=value^XH,h=l+XL;
    l=(l^(l>>30))*A; h=(h^(h>>30))*A; l=(l^(l>>27))*B; h=(h^(h>>27))*B;
    xr->lo=l^(l>>31); xr->hi=h^(h>>31);
}
__device__ __forceinline__ uint64_t xo_long(XoD *xr){
    uint64_t l=xr->lo,h=xr->hi; uint64_t n=xo_rotl(l+h,17)+l;
    h^=l; xr->lo=xo_rotl(l,49)^h^(h<<21); xr->hi=xo_rotl(h,28); return n;
}
__device__ __forceinline__ int xo_int(XoD *xr, uint32_t n){
    uint64_t r=(xo_long(xr)&0xFFFFFFFF)*n;
    if((uint32_t)r<n){ uint32_t lim=(~n+1)%n; while((uint32_t)r<lim) r=(xo_long(xr)&0xFFFFFFFF)*n; }
    return (int)(r>>32);
}
__device__ __forceinline__ double xo_double(XoD *xr){ return (xo_long(xr)>>(64-53))*1.1102230246251565E-16; }

/* exact port of xPerlinInit (noise.c) — fills one PerlinG octave */
__device__ void xo_perlinInit(PerlinG *p, XoD *xr){
    p->a = xo_double(xr)*256.0; p->b = xo_double(xr)*256.0; p->c = xo_double(xr)*256.0;
    p->amplitude = 1.0; p->lacunarity = 1.0;
    for(int i=0;i<256;i++) p->d[i]=(uint8_t)i;
    for(int i=0;i<256;i++){ int j=xo_int(xr,256-i)+i; uint8_t t=p->d[i]; p->d[i]=p->d[j]; p->d[j]=t; }
    p->d[256]=p->d[0];
    double i2=floor(p->b), d2=p->b-i2;
    p->h2=(uint8_t)(int)i2; p->d2=d2; p->t2=d2*d2*d2*(d2*(d2*6.0-15.0)+10.0);
}
/* exact port of xOctaveLegacyInit (noise.c) for the BLENDED case (end == 0 always:
 * omin == -(len-1), so the xSkipN branch is never taken). */
__device__ void xo_octaveLegacy(OctaveG *o, XoD *xr, PerlinG *oct, int omin, int len){
    int end=omin+len-1;                       /* == 0 for blended noise */
    double persist=1.0/(double)((1LL<<len)-1);
    double lacuna=pow(2.0,(double)end);       /* == 1 */
    xo_perlinInit(&oct[0], xr); oct[0].amplitude=persist; oct[0].lacunarity=lacuna;
    persist*=2.0; lacuna*=0.5;
    for(int i=1;i<len;i++){ xo_perlinInit(&oct[i], xr); oct[i].amplitude=persist; oct[i].lacunarity=lacuna; persist*=2.0; lacuna*=0.5; }
    o->octaves=oct; o->octcnt=len;
}
/* exact port of initBlendedNoise (biomenoise.c) — DIM_OVERWORLD */
__device__ void xo_initBlended(BlendedG *bn, uint64_t ws){
    bn->xzScale=0.25; bn->yScale=0.125; bn->xzFactor=80.0; bn->yFactor=160.0; bn->smearScaleMultiplier=8.0;
    bn->xzMultiplier=684.412*bn->xzScale; bn->yMultiplier=684.412*bn->yScale;
    bn->smearedYScale=bn->yMultiplier*bn->smearScaleMultiplier; bn->factoredSmearedYScale=bn->smearedYScale/bn->yFactor;
    const uint64_t md5t0=0x1ee555222ef96f14ULL, md5t1=0xe2bedfdbebe43d33ULL;
    XoD wsx; xo_set(&wsx, ws);
    uint64_t lo=xo_long(&wsx), hi=xo_long(&wsx);
    XoD xr; xr.lo=lo^md5t0; xr.hi=hi^md5t1;
    xo_octaveLegacy(&bn->octmin,  &xr, bn->oct+0,  -15, 16);
    xo_octaveLegacy(&bn->octmax,  &xr, bn->oct+16, -15, 16);
    xo_octaveLegacy(&bn->octmain, &xr, bn->oct+32, -7,  8);
}
/* copy ONE seed's blended noise back to the host for a bit-identity check */
__global__ void k_reseed_one(uint64_t seed, BlendedG *out){
    if(blockIdx.x*blockDim.x+threadIdx.x) return;
    BlendedG bn; xo_initBlended(&bn, seed); *out = bn;
}
/* throughput bench: re-seed the full blended noise per thread (store-all, ~12.5 KB
 * local/thread → reuses the validated sampler) */
__global__ void k_reseed_bench(uint64_t seed0, int n, unsigned char *out){
    int i=blockIdx.x*blockDim.x+threadIdx.x; if(i>=n) return;
    BlendedG bn; xo_initBlended(&bn, seed0+(uint64_t)i);
    out[i]=(unsigned char)(bn.oct[0].d[5]^bn.oct[39].d[200]^(int)bn.oct[20].a);
}

/* ── full per-thread re-seed: climate (depth/factor splines) + OTP (caves+jagged) ── */
__constant__ uint64_t XO_MD5OCT[17][2] = {
    {0xc613bf766619f992,0x954753f86691b86a},{0x7eee475a921c6cf5,0xf2bd39426f8da413},
    {0xfc0027cef9683114,0xb758d3954dcbfdd3},{0xd1fc8a05be565eca,0xdc2a3915cbdda25b},
    {0xb198de63a8012672,0x7b84cad43ef7b5a8},{0x0fd787bfbc403ec3,0x74a4a31ca21b48b8},
    {0x36d326eed40efeb2,0x5be9ce18223c636a},{0x082fe255f8be6631,0x4e96119e22dedc81},
    {0x0ef68ec68504005e,0x48b6bf93a2789640},{0xf11268128982754f,0x257a1d670430b0aa},
    {0xe51c98ce7d1de664,0x5f9478a733040c45},{0x6d7b49e7e429850a,0x2e3063c622a24777},
    {0xbd90d5377ba1b762,0xc07317d419a7548d},{0x53d39c6752dac858,0xbcd1c5a80ab65b3e},
    {0xb4a24d7a84e7677b,0x023ff9668e89b5c4},{0xdffa22b534c5f608,0xb9b67517d3665ca9},
    {0xd50708086cef4d7c,0x6e1651ecc7f43309}};
__device__ const double XO_LAC[17]={1,.5,.25,1./8,1./16,1./32,1./64,1./128,1./256,1./512,1./1024,1./2048,1./4096,1./8192,1./16384,1./32768,1./65536};
__device__ const double XO_PER[17]={0,1,2./3,4./7,8./15,16./31,32./63,64./127,128./255,256./511,512./1023,1024./2047,2048./4095,4096./8191,8192./16383,16384./32767,32768./65535};
__device__ const double XO_AMPI[17]={0,5./6,10./9,15./12,20./15,25./18,30./21,35./24,40./27,45./30,50./33,55./36,60./39,65./42,70./45,75./48,80./51};

__device__ int xo_octaveInit(OctaveG *o, XoD *xr, PerlinG *oct, const double *amp, int omin, int len, int nmax){
    double lacuna=XO_LAC[-omin], persist=XO_PER[len];
    uint64_t xlo=xo_long(xr), xhi=xo_long(xr);
    int i=0,n=0;
    for(; i<len && n!=nmax; i++, lacuna*=2.0, persist*=0.5){
        if(amp[i]==0.0) continue;
        XoD p; p.lo=xlo^XO_MD5OCT[16+omin+i][0]; p.hi=xhi^XO_MD5OCT[16+omin+i][1];
        xo_perlinInit(&oct[n], &p); oct[n].amplitude=amp[i]*persist; oct[n].lacunarity=lacuna; n++;
    }
    o->octaves=oct; o->octcnt=n; return n;
}
__device__ int xo_dpInit(DPerlinG *dpn, XoD *xr, PerlinG *oct, const double *amp, int omin, int len, int nmax){
    int na=-1,nb=-1; if(nmax>0){na=(nmax+1)>>1; nb=nmax-na;}
    int n=0;
    n+=xo_octaveInit(&dpn->octA, xr, oct+n, amp, omin, len, na);
    n+=xo_octaveInit(&dpn->octB, xr, oct+n, amp, omin, len, nb);
    int L=len,i;
    for(i=L-1;i>=0&&amp[i]==0.0;i--)L--;
    for(i=0;amp[i]==0.0;i++)L--;
    dpn->amplitude=XO_AMPI[L]; return n;
}
/* fills the whole TerrainNoiseG for `seed`; octbuf holds the climate+OTP octaves;
 * the 3 splines are seed-independent (uploaded once, shared). large=0 (non-large). */
__device__ void tg_reseed(TerrainNoiseG *P, PerlinG *octbuf, uint64_t seed,
                          SplineG *offS, SplineG *facS, SplineG *jagS){
    xo_initBlended(&P->base3dNoise, seed);
    XoD s; xo_set(&s, seed); uint64_t lo=xo_long(&s), hi=xo_long(&s);
    int n=0;
    #define CLIM(slot,ml,mh,omin,len,...) { const double a[]={__VA_ARGS__}; XoD p; p.lo=lo^ml; p.hi=hi^mh; n+=xo_dpInit(&P->climate[slot],&p,octbuf+n,a,omin,len,-1); }
    CLIM(TG_NP_SHIFT,0x080518cf6af25384ULL,0x3f3dfb40a54febd5ULL,-3,4, 1,1,1,0)
    CLIM(TG_NP_TEMP, 0x5c7e6b29735f0d7fULL,0xf7d86f1bbc734988ULL,-10,6, 1.5,0,1,0,0,0)
    CLIM(TG_NP_HUMID,0x81bb4d22e8dc168eULL,0xf1c8b4bea16303cdULL,-8,6, 1,1,0,0,0,0)
    CLIM(TG_NP_CONT, 0x83886c9d0ae3a662ULL,0xafa638a61b42e8adULL,-9,9, 1,1,2,2,2,1,1,1,1)
    CLIM(TG_NP_EROS, 0xd02491e6058f6fd8ULL,0x4792512c94c17a80ULL,-9,5, 1,1,0,1,1)
    CLIM(TG_NP_WEIRD,0xefc8ef4d36102b34ULL,0x1beeeb324a0f24eaULL,-7,6, 1,2,1,0,0,0)
    #undef CLIM
    #define OTP(slot,ml,mh,omin,len,...) { const double a[]={__VA_ARGS__}; XoD p; p.lo=lo^ml; p.hi=hi^mh; n+=xo_dpInit(&P->noises[slot],&p,octbuf+n,a,omin,len,-1); }
    OTP(TGO_PILLAR,                  0xd4defd1400fa5347ULL,0xccb6785451feaa1cULL,-7,2, 1.0,1.0)
    OTP(TGO_PILLAR_RARENESS,         0x8ddd6be7cd4b24d0ULL,0x0485e8665333197aULL,-8,1, 1.0)
    OTP(TGO_PILLAR_THICKNESS,        0x1a28cb2542f8308dULL,0xc4bba10b7fc7168cULL,-8,1, 1.0)
    OTP(TGO_SPAGHETTI_2D,            0x34748c98fa19c477ULL,0x2a02051eb9e9b9cdULL,-7,1, 1.0)
    OTP(TGO_SPAGHETTI_2D_ELEVATION,  0x29dd5fcf38d9edcdULL,0x5bb1d7a839f6d4abULL,-8,1, 1.0)
    OTP(TGO_SPAGHETTI_2D_MODULATOR,  0x95e07097e4685522ULL,0x0232deaf95eb5fedULL,-11,1, 1.0)
    OTP(TGO_SPAGHETTI_2D_THICKNESS,  0x37e5cb432b85f4c8ULL,0xe5e16b35b407aebcULL,-11,1, 1.0)
    OTP(TGO_SPAGHETTI_3D_1,          0xa5053f53ce3f5840ULL,0x834fa38a3ded87b7ULL,-7,1, 1.0)
    OTP(TGO_SPAGHETTI_3D_2,          0xb9a6367335a0ceefULL,0xd61ccfd0fac10ac3ULL,-7,1, 1.0)
    OTP(TGO_SPAGHETTI_3D_RARITY,     0xee4780c98d93a439ULL,0xbe45d334fdb76d2cULL,-11,1, 1.0)
    OTP(TGO_SPAGHETTI_3D_THICKNESS,  0x897e1f20ad2d2b27ULL,0xf4142f5a58d1d5abULL,-8,1, 1.0)
    OTP(TGO_SPAGHETTI_ROUGHNESS,     0x7aed57fd327d6591ULL,0xd495a3593e4d67bdULL,-5,1, 1.0)
    OTP(TGO_SPAGHETTI_ROUGHNESS_MODULATOR,0xe19387d2ceaf4eaaULL,0xcacdcd34e1bc2003ULL,-8,1, 1.0)
    OTP(TGO_CAVE_ENTRANCE,           0xf1008a17493d9a65ULL,0xbd1ce09e8bc70ea0ULL,-7,3, 0.4,0.5,1.0)
    OTP(TGO_CAVE_LAYER,              0x4dfe67be2ef51a83ULL,0x5a4ae8c4423e7206ULL,-8,1, 1.0)
    OTP(TGO_CAVE_CHEESE,             0xb159093bc7baaa50ULL,0x53abc45424417c20ULL,-8,9, 0.5,1.0,2.0,1.0,2.0,1.0,0.0,2.0,0.0)
    OTP(TGO_NOODLE,                  0xd23ce12b0c37e44bULL,0x66b1fdfbf6a474f3ULL,-8,1, 1.0)
    OTP(TGO_NOODLE_THICKNESS,        0x2fa49bbe949d4212ULL,0x5f82c95251d19891ULL,-8,1, 1.0)
    OTP(TGO_NOODLE_RIDGE_A,          0x86172ac1315f6026ULL,0x4a664470c7d7205fULL,-7,1, 1.0)
    OTP(TGO_NOODLE_RIDGE_B,          0xeb232b4b89fdde91ULL,0x031ee565ead84e5cULL,-7,1, 1.0)
    OTP(TGO_JAGGED,                  0xf902c0a7c9daa994ULL,0x71ecd96a8da5e503ULL,-16,16, 1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1)
    #undef OTP
    P->offsetSpline=offS; P->factorSpline=facS; P->jaggednessSpline=jagS;
}

#define TG_OCTBUF 160       /* climate(~46) + OTP(~90) octaves, headroom */
/* world Y of the air block above the surface at (X,Z), via the TESTED descend path
 * (same one the map uses): compute depth/factor/jagged on the 4 corner cells from the
 * reseeded P, then tg_surfaceDescend (early-exit, caves-aware). */
__device__ int tg_exacty_at(const TerrainNoiseG *P, int X, int Z, int caves){
    int cx=X>>2, cz=Z>>2;
    int ccx[4]={cx,cx,cx+1,cx+1}, ccz[4]={cz,cz+1,cz,cz+1};   /* g00,g01,g10,g11 */
    double dp[4],fc[4],jg[4];
    for(int c=0;c<4;c++){
        float np[4]; tg_noiseParams(P, ccx[c], ccz[c], np);
        dp[c]=tg_getSpline(P->offsetSpline, np) - 0.50375;
        fc[c]=tg_getSpline(P->factorSpline, np);
        double j=tg_sampleDP(&P->noises[TGO_JAGGED], (double)ccx[c]*4*1500.0, 0, (double)ccz[c]*4*1500.0);
        j = j>=0.0 ? j : j/2.0; j *= tg_getSpline(P->jaggednessSpline, np);
        jg[c]=j;
    }
    return tg_surfaceDescend(P, X, Z, cx, cz,
        dp[0],fc[0],jg[0], dp[1],fc[1],jg[1], dp[2],fc[2],jg[2], dp[3],fc[3],jg[3], caves) - 64;
}
/* batch Exact-Y: 1 thread / seed. Re-seed → surface Y at (X,Z) → in [ymin,ymax]?  */
__global__ void k_exacty(uint64_t seed0, int n, int X, int Z, int ymin, int ymax,
                         int invert, SplineG *offS, SplineG *facS, SplineG *jagS,
                         unsigned char *out){
    int i=blockIdx.x*blockDim.x+threadIdx.x; if(i>=n) return;
    TerrainNoiseG P; PerlinG octbuf[TG_OCTBUF];
    tg_reseed(&P, octbuf, seed0+(uint64_t)i, offS, facS, jagS);
    int y = tg_exacty_at(&P, X, Z, 0);   /* no-caves test */
    int in = (y>=ymin && y<=ymax);
    out[i] = (unsigned char)(invert ? !in : in);
}
/* debug: write the raw surface Y per seed (for the bit-identity test vs CPU) */
__global__ void k_exacty_y(uint64_t seed0, int n, int X, int Z,
                           SplineG *offS, SplineG *facS, SplineG *jagS, int *out){
    int i=blockIdx.x*blockDim.x+threadIdx.x; if(i>=n) return;
    TerrainNoiseG P; PerlinG octbuf[TG_OCTBUF];
    tg_reseed(&P, octbuf, seed0+(uint64_t)i, offS, facS, jagS);
    out[i] = tg_exacty_at(&P, X, Z, 0);   /* no-caves test */
}
/* staged diagnostic: stage 0=reseed only, 1=+noiseParams, 2=+getSpline, 3=+jagged DP,
 * 4=+full descend. Run each stage as a SEPARATE launch and check its sync error before
 * the context poisons → the first stage that faults names the culprit function. */
__global__ void k_dbg(uint64_t seed, int X, int Z, SplineG *offS, SplineG *facS, SplineG *jagS, int stage, float *out){
    if(blockIdx.x*blockDim.x+threadIdx.x) return;
    TerrainNoiseG P; PerlinG octbuf[TG_OCTBUF];
    tg_reseed(&P, octbuf, seed, offS, facS, jagS);
    out[7]=(float)P.base3dNoise.oct[0].d[10];
    if(stage<1) return;
    int cx=X>>2, cz=Z>>2; float np[4]; tg_noiseParams(&P, cx, cz, np);
    out[0]=np[0];
    if(stage<2) return;
    const SplineG *r = P.offsetSpline;                 /* stage 2: read ROOT (no ptr-follow) */
    out[1]=(float)r->len; out[2]=(float)r->typ; out[3]=r->loc[0];
    if(stage<3) return;
    const SplineG *c = r->val[0];                       /* stage 3: follow ONE child pointer */
    out[4]=(float)c->len;
    if(stage<4) return;
    out[5]=tg_getSpline(P.offsetSpline, np);            /* stage 4: full recursion */
}
#endif /* !TGPU_FP32 */

#ifdef TGPU_EXACTY_TEST
/* nvcc -O3 --fmad=false -DTGPU_EXACTY_TEST terrain_gpu.cu terrainnoise.c biomenoise.c \
 *      noise.c biomes.c layers.c generator.c util.c -o exacty_test  (then ./exacty_test)
 * Validates the FULL per-thread GPU Exact-Y (re-seed + surface) bit-identical vs CPU,
 * and measures throughput. --fmad=false is REQUIRED for bit-identity. */
#include <stdint.h>
/* flatten a CPU Spline DAG into a flat SplineG[] with MEMOISATION (cubiomes splines
 * share sub-trees — flattening each reference separately would blow the buffer and
 * corrupt the device pointers → illegal access). val[] hold child INDICES for now. */
static const Spline *g_seen[8192]; static int g_sidx[8192]; static int g_nseen;
static int spl_flat(const Spline *s, SplineG *arr, int *cnt){
    for(int j=0;j<g_nseen;j++) if(g_seen[j]==s) return g_sidx[j];   /* already done */
    int idx=(*cnt)++;
    g_seen[g_nseen]=s; g_sidx[g_nseen]=idx; g_nseen++;
    if(s->len==1){ arr[idx].len=1; ((FixSplineG*)&arr[idx])->val=((const FixSpline*)s)->val; return idx; }
    arr[idx].len=s->len; arr[idx].typ=s->typ;
    for(int i=0;i<s->len;i++){ arr[idx].loc[i]=s->loc[i]; arr[idx].der[i]=s->der[i]; }
    for(int i=0;i<s->len;i++){ int ci=spl_flat(s->val[i],arr,cnt); arr[idx].val[i]=(SplineG*)(intptr_t)ci; }
    return idx;
}
/* upload a CPU Spline DAG to the device, return the device root pointer */
static SplineG* spl_upload(const Spline *root){
    static SplineG arr[8192]; int cnt=0; g_nseen=0;
    spl_flat(root, arr, &cnt);                       /* root lands at index 0 */
    printf("  spline flattened: %d nodes\n", cnt);
    SplineG *d=NULL; cudaMalloc(&d, (size_t)cnt*sizeof(SplineG));
    for(int k=0;k<cnt;k++) if(arr[k].len!=1)
        for(int i=0;i<arr[k].len;i++) arr[k].val[i]=d+(intptr_t)arr[k].val[i];
    cudaMemcpy(d, arr, (size_t)cnt*sizeof(SplineG), cudaMemcpyHostToDevice);
    return d;
}
int main(){
    cudaDeviceProp pr; cudaGetDeviceProperties(&pr,0);
    printf("GPU: %s (%d SMs)\n", pr.name, pr.multiProcessorCount);
    /* deep call chain (surface→density→caves) + recursive tg_getSpline → the default
     * ~1 KB device stack overflows. Give each thread a generous stack. */
    cudaDeviceSetLimit(cudaLimitStackSize, 64*1024);
    int mc=MC_1_18, X=0, Z=0;
    TerrainNoise tn; setupTerrainNoise(&tn, mc, 0);
    initTerrainNoise(&tn, 1, DIM_OVERWORLD);   /* ensure g.bn.sp is built before upload */
    SplineG *dOff=spl_upload(tn.g.bn.sp), *dFac=spl_upload(tn.factorSpline), *dJag=spl_upload(tn.jaggednessSpline);
    cudaError_t e=cudaGetLastError(); if(e!=cudaSuccess){printf("spline upload err: %s\n",cudaGetErrorString(e)); return 1;}

    /* ── STAGED DIAGNOSTIC: pinpoint the faulting function ── */
    { float *dbg; cudaMalloc(&dbg,8*sizeof(float));
      const char *nm[5]={"0 reseed","1 +noiseParams","2 read-splineRoot","3 follow-childPtr","4 full-recursion"};
      for(int st=0; st<5; st++){
        cudaMemset(dbg,0,8*sizeof(float));
        k_dbg<<<1,32>>>(1, X, Z, dOff, dFac, dJag, st, dbg);
        cudaError_t se=cudaDeviceSynchronize();
        float h[8]; cudaMemcpy(h,dbg,8*sizeof(float),cudaMemcpyDeviceToHost);
        printf("[dbg stage %s]  sync=%s  | np0=%.4f rootLen=%.0f rootTyp=%.0f rootLoc0=%.4f childLen=%.0f spline=%.4f\n",
               nm[st], cudaGetErrorString(se), h[0],h[1],h[2],h[3],h[4],h[5]);
        if(se!=cudaSuccess){ printf("  => FAULT introduced at stage '%s'\n", nm[st]); break; }
      }
    }

    const int N=4096; uint64_t seed0=1;
    int *cpuY=(int*)malloc(N*sizeof(int));
    for(int i=0;i<N;i++){
        initTerrainNoise(&tn, seed0+i, DIM_OVERWORLD);
        int cx=X>>2,cz=Z>>2; double a[49],b[49],c[49],d[49];
        sampleNoiseColumnEx(&tn,cx,cz,a,0); sampleNoiseColumnEx(&tn,cx,cz+1,b,0);
        sampleNoiseColumnEx(&tn,cx+1,cz,c,0); sampleNoiseColumnEx(&tn,cx+1,cz+1,d,0);
        int blk[384]; cpuY[i]=generateColumn(X,Z,blk,a,b,c,d,1)-64;   /* no-caves ref */
    }
    int *dY; cudaMalloc(&dY,N*sizeof(int)); int blk=64;
    k_exacty_y<<<(N+blk-1)/blk,blk>>>(seed0,N,X,Z,dOff,dFac,dJag,dY);
    cudaError_t ke=cudaGetLastError(), se=cudaDeviceSynchronize();
    if(ke!=cudaSuccess || se!=cudaSuccess)
        printf("k_exacty_y  launch=%s  sync=%s\n", cudaGetErrorString(ke), cudaGetErrorString(se));
    int *gpuY=(int*)malloc(N*sizeof(int)); cudaMemcpy(gpuY,dY,N*sizeof(int),cudaMemcpyDeviceToHost);
    int diffs=0,maxd=0;
    for(int i=0;i<N;i++){ int dd=gpuY[i]-cpuY[i]; if(dd){ diffs++; int ad=dd<0?-dd:dd; if(ad>maxd)maxd=ad;
        if(diffs<=8) printf("  seed=%llu  GPU=%d  CPU=%d\n",(unsigned long long)(seed0+i),gpuY[i],cpuY[i]); } }
    printf("Exact-Y surface (caves): %d/%d diffs, max|d|=%d  => %s\n",
           diffs,N,maxd, diffs==0?"GPU == CPU BIT-IDENTICAL ✓":"DIFF a corriger");

    const int M=4*1024*1024; unsigned char *dO; cudaMalloc(&dO,M);
    k_exacty<<<(M+blk-1)/blk,blk>>>(seed0,M,X,Z,60,80,0,dOff,dFac,dJag,dO); cudaDeviceSynchronize();
    cudaEvent_t a0,a1; cudaEventCreate(&a0); cudaEventCreate(&a1);
    cudaEventRecord(a0); k_exacty<<<(M+blk-1)/blk,blk>>>(seed0,M,X,Z,60,80,0,dOff,dFac,dJag,dO);
    cudaEventRecord(a1); cudaEventSynchronize(a1);
    float ms=0; cudaEventElapsedTime(&ms,a0,a1);
    printf("Exact-Y throughput (caves, single point @0,0): %.2f Mseeds/s (%.0f ms / %d)\n",
           (double)M/(ms/1000.0)/1e6, ms, M);
    e=cudaGetLastError(); if(e!=cudaSuccess) printf("kernel err: %s\n",cudaGetErrorString(e));
    return 0;
}
#endif

#ifdef TGPU_RESEED_TEST
/* nvcc -O3 -DTGPU_RESEED_TEST terrain_gpu.cu terrainnoise.c biomenoise.c noise.c \
 *      biomes.c layers.c generator.c util.c -o reseed_test   (then ./reseed_test) */
int main(void){
    cudaDeviceProp pr; cudaGetDeviceProperties(&pr,0);
    printf("GPU: %s (%d SMs)\n", pr.name, pr.multiProcessorCount);
    uint64_t seed = 123456789ULL;
    /* CPU reference: the real cubiomes blended noise for this seed */
    TerrainNoise tn; setupTerrainNoise(&tn, MC_1_18, 0); initTerrainNoise(&tn, seed, DIM_OVERWORLD);
    BlendedNoise *cpu = &tn.base3dNoise;
    /* GPU re-seed for the same seed */
    BlendedG *dB, hB; cudaMalloc(&dB, sizeof(BlendedG));
    k_reseed_one<<<1,32>>>(seed, dB); cudaDeviceSynchronize();
    cudaMemcpy(&hB, dB, sizeof(BlendedG), cudaMemcpyDeviceToHost);
    int diffs=0;
    for(int o=0;o<40;o++){
        PerlinNoise *pc=&cpu->oct[o]; PerlinG *pg=&hB.oct[o];
        for(int k=0;k<257;k++) if(pc->d[k]!=pg->d[k]) diffs++;
        if(pc->a!=pg->a||pc->b!=pg->b||pc->c!=pg->c) diffs++;
        if(pc->amplitude!=pg->amplitude||pc->lacunarity!=pg->lacunarity) diffs++;
        if(pc->h2!=pg->h2||pc->d2!=pg->d2||pc->t2!=pg->t2) diffs++;
    }
    printf("blended re-seed: %s (%d field diffs over 40 octaves)\n",
           diffs==0?"GPU == CPU BIT-IDENTICAL ✓":"DIFF — bug a corriger", diffs);
    /* throughput */
    const int N=16*1024*1024; unsigned char *dO; cudaMalloc(&dO,N);
    int blk=128, grid=(N+blk-1)/blk;
    k_reseed_bench<<<grid,blk>>>(1ULL,N,dO); cudaDeviceSynchronize();
    cudaEvent_t a,b; cudaEventCreate(&a); cudaEventCreate(&b);
    cudaEventRecord(a); k_reseed_bench<<<grid,blk>>>(7ULL,N,dO); cudaEventRecord(b); cudaEventSynchronize(b);
    float ms=0; cudaEventElapsedTime(&ms,a,b);
    printf("blended re-seed throughput (store-all, 40 oct): %.1f Mseeds/s (%.0f ms / %d)\n",
           (double)N/(ms/1000.0)/1e6, ms, N);
    cudaError_t e=cudaGetLastError(); if(e!=cudaSuccess) printf("cuda err: %s\n", cudaGetErrorString(e));
    return 0;
}
#endif

#ifdef TGPU_TEST
/* nvcc -O3 -DTGPU_TEST terrain_gpu.cu terrainnoise.c biomenoise.c noise.c biomes.c \
 *      layers.c generator.c util.c -o tgpu_test    (then ./tgpu_test) */
int main(void) {
    uint64_t seed = 100; int mc = MC_1_18;
    /* near + FAR reference points: the far ones expose the FP32 large-coord drift */
    int C[8][2] = {{0,0},{532,286},{113,126},{2,32},{357,281},
                   {100000,100000},{1000000,-500000},{12993810,-25889529}};
    const int N = 8;
    int gpu[N], gpuNC[N];
    for (int i=0;i<N;i++) tgpu_surface_grid(mc, seed, C[i][0], C[i][1], 1, 1, 1, 1, &gpu[i]);
    for (int i=0;i<N;i++) tgpu_surface_grid(mc, seed, C[i][0], C[i][1], 1, 1, 1, 0, &gpuNC[i]);
    /* CPU FP64 reference */
    TerrainNoise tn; setupTerrainNoise(&tn,mc,0); initTerrainNoise(&tn,seed,DIM_OVERWORLD);
    int maxdiff = 0, maxnear = 0, maxdiffNC = 0;
    for (int i=0;i<N;i++){ int x=C[i][0],z=C[i][1],cx=x>>2,cz=z>>2;
        double a[49],b[49],c[49],d[49];
        sampleNoiseColumn(&tn,cx,cz,a); sampleNoiseColumn(&tn,cx,cz+1,b);
        sampleNoiseColumn(&tn,cx+1,cz,c); sampleNoiseColumn(&tn,cx+1,cz+1,d);
        int blk[384]; int cpu = generateColumn(x,z,blk,a,b,c,d,1)-64;
        int diff = gpu[i]-cpu; int ad = diff<0?-diff:diff;
        if (ad>maxdiff) maxdiff=ad;
        if (i<6 && ad>maxnear) maxnear=ad;   /* first 6 = within ~100k blocks */
        /* NO-CAVES reference */
        double a0[49],b0[49],c0[49],d0[49];
        sampleNoiseColumnEx(&tn,cx,cz,a0,0); sampleNoiseColumnEx(&tn,cx,cz+1,b0,0);
        sampleNoiseColumnEx(&tn,cx+1,cz,c0,0); sampleNoiseColumnEx(&tn,cx+1,cz+1,d0,0);
        int cpuNC = generateColumn(x,z,blk,a0,b0,c0,d0,1)-64;
        int dNC = gpuNC[i]-cpuNC; int aNC = dNC<0?-dNC:dNC;
        if (i<6 && aNC>maxdiffNC) maxdiffNC=aNC;
        printf("(%9d,%9d)  GPU=%4d CPU=%4d d=%d | noCaves GPU=%4d CPU=%4d d=%d\n",
               x,z,gpu[i],cpu,diff, gpuNC[i],cpuNC,dNC);
    }
    printf("no-caves: max|GPU-CPU| near = %d blocks\n", maxdiffNC);
#ifdef TGPU_FP32
    printf("\nMODE FP32 (map): max|GPU-CPU| near spawn (<100k) = %d, overall = %d blocks\n", maxnear, maxdiff);
    printf("%s\n", maxnear<=2 ? "=> FP32 OK (near-spawn essentially exact; far points drift by design)"
                              : "=> FP32 drift too large near spawn — a corriger");
#else
    printf("\n%s\n", maxdiff==0 ? "=> FP64: GPU == CPU bit-exact (valid)" : "=> FP64: DIFF a corriger");
#endif
    return 0;
}
#endif
#endif /* __CUDACC__ */
