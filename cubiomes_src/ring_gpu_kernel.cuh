// ring_gpu_kernel.cuh — (19/10) GPU scan of the positions of a spiral iterator.
//
// For ONE seed, tests every position of a top-level spiral against the parts
// of its subtree that are climate-only, and keeps the positions that may pass.
// The C engine then evaluates the WHOLE session at those positions only
// (ssa_test_seeds_cand), so the results are exactly the CPU's: the GPU only
// has to never reject a position the CPU would accept (recall-safe):
//   * point tests (Locate climate extreme on a point, all octaves): the exact
//     sample in FP32, bounds widened by a small margin;
//   * « at least k of n » OR of box tests:
//       kind 0  climate extreme in a box   — G×G grid, bound widened (margin)
//       kind 1  biome sample in a box      — G×G grid, a point counts when its
//               (T,H,C,E,W) is inside one of the biomes' climate boxes, the
//               boxes widened (margin)
//     a box counts as soon as one grid point does; the OR keeps the position
//     when at least k boxes count.
// Everything else of the subtree is left to the C engine.
//
// Compiles as plain C (GPUDEV empty) for the validation on the CPU, and as
// CUDA in noise_gpu.cu. Needs noise_gpu_kernel.cuh first.
#ifndef RING_GPU_KERNEL_CUH
#define RING_GPU_KERNEL_CUH

#define RG_MAXPT  12
#define RG_MAXOR  4
#define RG_MAXBOX 16
#define RG_MAXBB  64
#define RG_INF    2000000000

// Rg_fdiv / Rg_prepare also run on the host (the launcher prepares the grid)
#ifdef __CUDACC__
  #define RGHD __host__ __device__
#else
  #define RGHD
#endif

typedef struct { int para, lo, hi; } RgPt;                 // ×10000, inclusive
typedef struct { int x1, z1, x2, z2; } RgBox;               // blocks, relative
typedef struct { int lo[5], hi[5]; } RgBB;                  // T,H,C,E,W ×10000
typedef struct {
    int kind, k, nbox, grid;          // kind 0: climate box, 1: biome membership
    int para, lo, hi;                 // kind 0
    int nbb;                          // kind 1
    RgBox box[RG_MAXBOX];
    RgBB  bb[RG_MAXBB];
} RgOr;
typedef struct {
    int npt; RgPt pt[RG_MAXPT];
    int nor; RgOr ors[RG_MAXOR];
    int x1, z1, x2, z2, step;         // the spiral's square (absolute, parent at 0, 0)
    int rx1, rz1, nx, nz;             // its grid (filled by Rg_prepare)
} RgSpec;

// cubiomes-viewer / session_engine floordiv
RGHD static inline int Rg_fdiv(int a, int b){
    return a / b - ((a % b != 0) && ((a < 0) != (b < 0)));
}
RGHD static void Rg_prepare(RgSpec *s){
    int st = s->step > 0 ? s->step : 512;
    s->rx1 = Rg_fdiv(s->x1, st); s->rz1 = Rg_fdiv(s->z1, st);
    s->nx = Rg_fdiv(s->x2, st) - s->rx1 + 1;
    s->nz = Rg_fdiv(s->z2, st) - s->rz1 + 1;
    if (s->nx < 0) s->nx = 0;
    if (s->nz < 0) s->nz = 0;
}

// (19/10 ter) Ng_perlin32 with a BRANCHLESS gradient: Ng_ilf's 16-way switch,
// indexed by the permutation, made the 32 threads of a warp take up to 16
// different paths 8 times per sample (36 M positions/s on an RTX 5080). Same
// sums (components ±1 / 0, exact): the result is bit-identical.
GPUDEV static inline float Rg_grad(uint8_t idx, float a, float b, float c){
    int h = idx & 15;
    float u = h < 8 ? a : b;
    float v = h < 4 ? b : ((h == 12 || h == 14) ? a : c);
    return ((h & 1) ? -u : u) + ((h & 2) ? -v : v);
}
GPUDEV static float Rg_perlin32(const PerlinG *p, double d1, double d2, double d3){
    uint8_t h1,h2,h3; float t1,t2,t3,f1,f2,f3;
    if (d2 == 0.0){ f2=(float)p->d2; h2=p->h2; t2=(float)p->t2; }
    else { d2+=p->b; double i2=floor(d2); f2=(float)(d2-i2); h2=(uint8_t)(int)i2; t2=f2*f2*f2*(f2*(f2*6-15)+10); }
    d1+=p->a; d3+=p->c; double i1=floor(d1), i3=floor(d3);
    f1=(float)(d1-i1); f3=(float)(d3-i3); h1=(uint8_t)(int)i1; h3=(uint8_t)(int)i3;
    t1=f1*f1*f1*(f1*(f1*6-15)+10); t3=f3*f3*f3*(f3*(f3*6-15)+10);
    const uint8_t *idx=p->d;
    uint8_t a1=idx[h1]+h2,b1=idx[h1+1]+h2,a2=idx[a1]+h3,b2=idx[b1]+h3,a3=idx[a1+1]+h3,b3=idx[b1+1]+h3;
    float l1=Rg_grad(idx[a2],f1,f2,f3),   l2=Rg_grad(idx[b2],f1-1,f2,f3);
    float l3=Rg_grad(idx[a3],f1,f2-1,f3), l4=Rg_grad(idx[b3],f1-1,f2-1,f3);
    float l5=Rg_grad(idx[a2+1],f1,f2,f3-1),   l6=Rg_grad(idx[b2+1],f1-1,f2,f3-1);
    float l7=Rg_grad(idx[a3+1],f1,f2-1,f3-1), l8=Rg_grad(idx[b3+1],f1-1,f2-1,f3-1);
    l1=Ng_lf(t1,l1,l2); l3=Ng_lf(t1,l3,l4); l5=Ng_lf(t1,l5,l6); l7=Ng_lf(t1,l7,l8);
    l1=Ng_lf(t2,l1,l3); l5=Ng_lf(t2,l5,l7);
    return Ng_lf(t3,l1,l5);
}
GPUDEV static float Rg_oct32(const PerlinG *pool, const OctaveG *o, double x, double y, double z){
    float v = 0;
    for (int i = 0; i < o->octcnt; i++){
        const PerlinG *p = &pool[o->oct_off + i]; double L = p->lacunarity;
        v += (float)p->amplitude * Rg_perlin32(p, x*L, y*L, z*L);
    }
    return v;
}
GPUDEV static float Rg_dp32(const PerlinG *pool, const DPerlinG *n, double x, double y, double z){
    const double f = 337.0/331.0;
    return (Rg_oct32(pool, &n->octA, x, y, z) + Rg_oct32(pool, &n->octB, x*f, y*f, z*f)) * (float)n->amplitude;
}

// (19/10 quater) the hot path WITHOUT double precision. A consumer GPU does
// FP64 at 1/64 of the FP32 rate and Ng_perlin32 needs ~12 FP64 operations per
// sample (x·L + a, floor, the fraction). Here x is an integer (1:4 cell) and the
// scale (L, or L·337/331 for the B stream) and the offset a are split into two
// floats each (hi + lo): x·s + a is rebuilt with an exact product error (fmaf)
// and an exact sum (TwoSum) — about 1e-12 relative, i.e. the same lattice cell
// and the same fraction as the FP64 code except within ~1e-7 of a cell edge
// (covered by the margins). Measured against Ng_dp32: tests/ring_gpu_check.py.
typedef struct { float sh, sl, ah, al, ch, cl, amp, t2; uint8_t h2, pad[3]; float d2; } RgOctF;
typedef struct { const BiomeNoiseG *bn; const RgOctF *of; } RgN;

// octave k of the pool: its scale (A: L, B: L·337/331) and offsets as float pairs
GPUDEV static void Rg_octF(const BiomeNoiseG *bn, int k, RgOctF *o){
    const PerlinG *p = &bn->oct[k];
    int inB = 0;
    for (int c = 0; c < 6; c++){
        const OctaveG *b = &bn->climate[c].octB;
        if (k >= b->oct_off && k < b->oct_off + b->octcnt) inB = 1;
    }
    double sc = inB ? p->lacunarity * (337.0 / 331.0) : p->lacunarity;
    o->sh = (float)sc;   o->sl = (float)(sc - (double)o->sh);
    o->ah = (float)p->a; o->al = (float)(p->a - (double)o->ah);
    o->ch = (float)p->c; o->cl = (float)(p->c - (double)o->ch);
    o->amp = (float)p->amplitude;
    o->d2 = (float)p->d2; o->t2 = (float)p->t2; o->h2 = p->h2;
}
// exactly rounded FP32 operations: nvcc must not fuse them into FMAs (that
// would break the exact error terms below)
#ifdef __CUDACC__
  #define RG_ADD(a, b) __fadd_rn((a), (b))
  #define RG_SUB(a, b) __fsub_rn((a), (b))
  #define RG_MUL(a, b) __fmul_rn((a), (b))
#else
  #define RG_ADD(a, b) ((a) + (b))
  #define RG_SUB(a, b) ((a) - (b))
  #define RG_MUL(a, b) ((a) * (b))
#endif
// v = i·s + a  ->  lattice index (& 255) and fraction, in FP32 only
GPUDEV static inline void Rg_coord(int i, float sh, float sl, float ah, float al,
                                   uint8_t *h, float *f){
    float xf = (float)i;                        // exact (|i| < 2^24)
    float pr = RG_MUL(xf, sh);
    float pe = fmaf(xf, sh, -pr);               // exact error of the product
    pe = fmaf(xf, sl, pe);
    float t = RG_ADD(pr, ah);                   // TwoSum(pr, ah)
    float bp = RG_SUB(t, pr);
    float te = RG_ADD(RG_SUB(pr, RG_SUB(t, bp)), RG_SUB(ah, bp));
    float lo = te + pe + al;
    float fl = floorf(t);
    float fr = RG_SUB(t, fl) + lo;
    if (fr < 0.0f) { fr += 1.0f; fl -= 1.0f; }
    else if (fr >= 1.0f) { fr -= 1.0f; fl += 1.0f; }
    *h = (uint8_t)(int)fl; *f = fr;
}
GPUDEV static float Rg_perlinF(const PerlinG *p, const RgOctF *o, int qx, int qz){
    uint8_t h1, h2 = o->h2, h3; float f1, f2 = o->d2, f3, t1, t2 = o->t2, t3;
    Rg_coord(qx, o->sh, o->sl, o->ah, o->al, &h1, &f1);
    Rg_coord(qz, o->sh, o->sl, o->ch, o->cl, &h3, &f3);
    t1=f1*f1*f1*(f1*(f1*6-15)+10); t3=f3*f3*f3*(f3*(f3*6-15)+10);
    const uint8_t *idx=p->d;
    uint8_t a1=idx[h1]+h2,b1=idx[h1+1]+h2,a2=idx[a1]+h3,b2=idx[b1]+h3,a3=idx[a1+1]+h3,b3=idx[b1+1]+h3;
    float l1=Rg_grad(idx[a2],f1,f2,f3),   l2=Rg_grad(idx[b2],f1-1,f2,f3);
    float l3=Rg_grad(idx[a3],f1,f2-1,f3), l4=Rg_grad(idx[b3],f1-1,f2-1,f3);
    float l5=Rg_grad(idx[a2+1],f1,f2,f3-1),   l6=Rg_grad(idx[b2+1],f1-1,f2,f3-1);
    float l7=Rg_grad(idx[a3+1],f1,f2-1,f3-1), l8=Rg_grad(idx[b3+1],f1-1,f2-1,f3-1);
    l1=Ng_lf(t1,l1,l2); l3=Ng_lf(t1,l3,l4); l5=Ng_lf(t1,l5,l6); l7=Ng_lf(t1,l7,l8);
    l1=Ng_lf(t2,l1,l3); l5=Ng_lf(t2,l5,l7);
    return Ng_lf(t3,l1,l5);
}
GPUDEV static float Rg_dpF(const RgN *n, int para, int qx, int qz){
    const DPerlinG *d = &n->bn->climate[para];
    float va = 0, vb = 0;
    for (int i = 0; i < d->octA.octcnt; i++){
        int k = d->octA.oct_off + i;
        va += n->of[k].amp * Rg_perlinF(&n->bn->oct[k], &n->of[k], qx, qz);
    }
    for (int i = 0; i < d->octB.octcnt; i++){
        int k = d->octB.oct_off + i;
        vb += n->of[k].amp * Rg_perlinF(&n->bn->oct[k], &n->of[k], qx, qz);
    }
    return (va + vb) * (float)d->amplitude;
}

// the climate value ×10000 of parameter `para` (0 T, 1 H, 2 C, 3 E, 5 W) at
// block (x, z): the engine's point sample, (x >> 2, 0, z >> 2), no shift
GPUDEV static inline int Rg_val(const RgN *n, int para, int qx, int qz){
    return (int)(10000.0f * Rg_dpF(n, para, qx, qz));
}

// one grid point of a biome box: inside one of the climate boxes? (lazy params)
GPUDEV static int Rg_member(const RgN *bn, const RgOr *o, int qx, int qz){
    // order: H, W, T, E, C (cheap and selective first); 5 = « not computed »
    const int order[5] = {1, 4, 0, 3, 2};      // index into lo/hi: T0 H1 C2 E3 W4
    const int para[5]  = {0, 1, 2, 3, 5};
    int have = 0, v[5] = {0, 0, 0, 0, 0};
    unsigned long long alive = 0;               // candidate boxes still possible
    for (int b = 0; b < o->nbb && b < 64; b++) alive |= 1ULL << b;
    for (int s = 0; s < 5 && alive; s++){
        int k = order[s];
        v[k] = Rg_val(bn, para[k], qx, qz); have |= 1 << k;
        for (int b = 0; b < o->nbb && b < 64; b++)
            if ((alive >> b) & 1ULL)
                if (v[k] < o->bb[b].lo[k] || v[k] > o->bb[b].hi[k]) alive &= ~(1ULL << b);
    }
    (void)have;
    return alive != 0;
}

// box `bi` of OR `o` around (x, z): one grid point passes?
GPUDEV static int Rg_box(const RgN *bn, const RgOr *o, int bi, int x, int z){
    const RgBox *b = &o->box[bi];
    int qx1 = (x + b->x1) >> 2, qz1 = (z + b->z1) >> 2;
    int qx2 = (x + b->x2) >> 2, qz2 = (z + b->z2) >> 2;
    int g = o->grid < 2 ? 2 : o->grid;
    for (int j = 0; j < g; j++){
        int qz = qz1 + (int)(((long long)(qz2 - qz1) * j) / (g - 1));
        for (int i = 0; i < g; i++){
            int qx = qx1 + (int)(((long long)(qx2 - qx1) * i) / (g - 1));
            if (o->kind == 0){
                int v = Rg_val(bn, o->para, qx, qz);
                if (v >= o->lo && v <= o->hi) return 1;
            } else if (Rg_member(bn, o, qx, qz)) {
                return 1;
            }
        }
    }
    return 0;
}

// may the subtree pass at block (x, z)?
GPUDEV static int Rg_testPos(const RgN *bn, const RgSpec *s, int x, int z){
    int qx = x >> 2, qz = z >> 2;
    for (int i = 0; i < s->npt; i++){
        int v = Rg_val(bn, s->pt[i].para, qx, qz);
        if (v < s->pt[i].lo || v > s->pt[i].hi) return 0;
    }
    for (int r = 0; r < s->nor; r++){
        const RgOr *o = &s->ors[r];
        int got = 0;
        for (int bi = 0; bi < o->nbox; bi++){
            if (Rg_box(bn, o, bi, x, z)) { if (++got >= o->k) break; }
            if (got + (o->nbox - bi - 1) < o->k) return 0;
        }
        if (got < o->k) return 0;
    }
    return 1;
}

// grid index g (0 .. nx*nz-1) -> position; 0 when outside the square
GPUDEV static inline int Rg_pos(const RgSpec *s, long long g, int *x, int *z){
    int rx = s->rx1 + (int)(g % s->nx), rz = s->rz1 + (int)(g / s->nx);
    long long bx = (long long)rx * s->step, bz = (long long)rz * s->step;
    if (bx < s->x1 || bx > s->x2 || bz < s->z1 || bz > s->z2) return 0;
    *x = (int)bx; *z = (int)bz;
    return 1;
}

#endif // RING_GPU_KERNEL_CUH
