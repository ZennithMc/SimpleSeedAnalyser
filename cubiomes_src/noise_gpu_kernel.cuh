// noise_gpu_kernel.cuh — device port of the 1.18+ noise biome generator.
// Compiles as plain C (GPUDEV/GPUMEM empty) for bit-exact validation, and as
// CUDA (__device__/__constant__) for the real kernel. All seed-independent data
// (flat spline, biome-tree tables) is passed in as pointers, so the same code
// runs whether those live in host memory (validation) or device memory (CUDA).
//
// The per-seed octave pool is referenced by INTEGER OFFSET (not pointer): this
// lets the whole BiomeNoiseG state live in CUDA shared memory safely (a pointer
// to shared stored inside shared memory is the classic CUDA footgun).
#ifndef NOISE_GPU_KERNEL_CUH
#define NOISE_GPU_KERNEL_CUH
#include <stdint.h>
#include <math.h>

#ifdef __CUDACC__
  #define GPUDEV __device__
  #define GPUMEM __constant__
#else
  #define GPUDEV
  #define GPUMEM
#endif

// ----------------------------------------------------------------------------
// device structs (octaves referenced by offset into the seed's oct[] pool)
// ----------------------------------------------------------------------------
typedef struct { uint64_t lo, hi; } XoroG;
typedef struct {
    uint8_t d[257]; uint8_t h2;
    double a, b, c, amplitude, lacunarity, d2, t2;
} PerlinG;
typedef struct { int octcnt; int oct_off; } OctaveG;     // octaves at pool[oct_off ..]
typedef struct { double amplitude; OctaveG octA, octB; } DPerlinG;
// NP order: 0=TEMP 1=HUMID 2=CONT 3=EROS 4=SHIFT(=DEPTH offset) 5=WEIRD
typedef struct { DPerlinG climate[6]; PerlinG oct[46]; int mc; } BiomeNoiseG;

// flattened spline node (cubiomes pointer tree -> indexable array)
typedef struct { int len, typ; float fixval; float loc[12], der[12]; int val[12]; } SplineFlatG;
// mirrors cubiomes BiomeTree; arrays may live in host or device memory
typedef struct {
    const uint32_t *steps; const int32_t *param; const uint64_t *nodes;
    uint32_t order; uint32_t len;
} BiomeTreeG;

// ----------------------------------------------------------------------------
// Xoroshiro128++ (rng.h)
// ----------------------------------------------------------------------------
GPUDEV static inline uint64_t Xg_rotl(uint64_t x, int b){ return (x << b) | (x >> (64 - b)); }
GPUDEV static inline void Xg_set(XoroG *xr, uint64_t value){
    const uint64_t XL=0x9e3779b97f4a7c15ULL, XH=0x6a09e667f3bcc909ULL;
    const uint64_t A=0xbf58476d1ce4e5b9ULL, B=0x94d049bb133111ebULL;
    uint64_t l = value ^ XH, h = l + XL;
    l = (l ^ (l >> 30)) * A; h = (h ^ (h >> 30)) * A;
    l = (l ^ (l >> 27)) * B; h = (h ^ (h >> 27)) * B;
    l = l ^ (l >> 31);       h = h ^ (h >> 31);
    xr->lo = l; xr->hi = h;
}
GPUDEV static inline uint64_t Xg_long(XoroG *xr){
    uint64_t l = xr->lo, h = xr->hi;
    uint64_t n = Xg_rotl(l + h, 17) + l;
    h ^= l;
    xr->lo = Xg_rotl(l, 49) ^ h ^ (h << 21);
    xr->hi = Xg_rotl(h, 28);
    return n;
}
GPUDEV static inline int Xg_int(XoroG *xr, uint32_t n){
    uint64_t r = (Xg_long(xr) & 0xFFFFFFFF) * n;
    if ((uint32_t)r < n)
        while ((uint32_t)r < (~n + 1) % n)
            r = (Xg_long(xr) & 0xFFFFFFFF) * n;
    return r >> 32;
}
GPUDEV static inline double Xg_dbl(XoroG *xr){
    return (Xg_long(xr) >> (64 - 53)) * 1.1102230246251565E-16;
}

// ----------------------------------------------------------------------------
// per-seed octave init (noise.c / biomenoise.c)
// ----------------------------------------------------------------------------
GPUMEM static const uint64_t NG_MD5OCT[13][2] = {
    {0xb198de63a8012672ULL,0x7b84cad43ef7b5a8ULL},{0x0fd787bfbc403ec3ULL,0x74a4a31ca21b48b8ULL},
    {0x36d326eed40efeb2ULL,0x5be9ce18223c636aULL},{0x082fe255f8be6631ULL,0x4e96119e22dedc81ULL},
    {0x0ef68ec68504005eULL,0x48b6bf93a2789640ULL},{0xf11268128982754fULL,0x257a1d670430b0aaULL},
    {0xe51c98ce7d1de664ULL,0x5f9478a733040c45ULL},{0x6d7b49e7e429850aULL,0x2e3063c622a24777ULL},
    {0xbd90d5377ba1b762ULL,0xc07317d419a7548dULL},{0x53d39c6752dac858ULL,0xbcd1c5a80ab65b3eULL},
    {0xb4a24d7a84e7677bULL,0x023ff9668e89b5c4ULL},{0xdffa22b534c5f608ULL,0xb9b67517d3665ca9ULL},
    {0xd50708086cef4d7cULL,0x6e1651ecc7f43309ULL},
};
GPUMEM static const double NG_LAC[]  = {1,.5,.25,1./8,1./16,1./32,1./64,1./128,1./256,1./512,1./1024,1./2048,1./4096};
GPUMEM static const double NG_PER[]  = {0,1,2./3,4./7,8./15,16./31,32./63,64./127,128./255,256./511};
GPUMEM static const double NG_AMPI[] = {0,5./6,10./9,15./12,20./15,25./18,30./21,35./24,40./27,45./30};

GPUDEV static void Ng_perlinInit(PerlinG *p, XoroG *xr){
    p->a = Xg_dbl(xr) * 256.0; p->b = Xg_dbl(xr) * 256.0; p->c = Xg_dbl(xr) * 256.0;
    p->amplitude = 1.0; p->lacunarity = 1.0;
    uint8_t *idx = p->d;
    for (int i = 0; i < 256; i++) idx[i] = (uint8_t)i;
    for (int i = 0; i < 256; i++){
        int j = Xg_int(xr, 256 - i) + i;
        uint8_t n = idx[i]; idx[i] = idx[j]; idx[j] = n;
    }
    idx[256] = idx[0];
    double i2 = floor(p->b), d2 = p->b - i2;
    p->h2 = (uint8_t)(int)i2; p->d2 = d2;
    p->t2 = d2*d2*d2*(d2*(d2*6.0-15.0)+10.0);
}
// fills pool[base .. base+count) ; sets o->oct_off=base, o->octcnt=count ; returns count
GPUDEV static int Ng_octInit(PerlinG *pool, int base, OctaveG *o, XoroG *xr,
                             const double *amp, int omin, int len, int nmax){
    double lacuna = NG_LAC[-omin], persist = NG_PER[len];
    uint64_t xlo = Xg_long(xr), xhi = Xg_long(xr);
    int i = 0, n = 0;
    for (; i < len && n != nmax; i++, lacuna *= 2.0, persist *= 0.5){
        if (amp[i] == 0) continue;
        XoroG pxr;
        pxr.lo = xlo ^ NG_MD5OCT[12 + omin + i][0];
        pxr.hi = xhi ^ NG_MD5OCT[12 + omin + i][1];
        Ng_perlinInit(&pool[base + n], &pxr);
        pool[base + n].amplitude = amp[i] * persist;
        pool[base + n].lacunarity = lacuna;
        n++;
    }
    o->oct_off = base; o->octcnt = n;
    return n;
}
GPUDEV static int Ng_dpInit(PerlinG *pool, int base, DPerlinG *dp, XoroG *xr,
                            const double *amp, int omin, int len, int nmax){
    int i, n = 0, na = -1, nb = -1;
    if (nmax > 0){ na = (nmax + 1) >> 1; nb = nmax - na; }
    n += Ng_octInit(pool, base + n, &dp->octA, xr, amp, omin, len, na);
    n += Ng_octInit(pool, base + n, &dp->octB, xr, amp, omin, len, nb);
    for (i = len - 1; i >= 0 && amp[i] == 0.0; i--) len--;
    for (i = 0; amp[i] == 0.0; i++) len--;
    dp->amplitude = NG_AMPI[len];
    return n;
}
// init one climate parameter (large-biomes = 0, the normal world type)
GPUDEV static int Ng_climate(PerlinG *pool, int base, DPerlinG *dpn,
                             uint64_t xlo, uint64_t xhi, int nptype){
    XoroG pxr; int n = 0;
    if (nptype == 4){ const double amp[]={1,1,1,0};
        pxr.lo=xlo^0x080518cf6af25384ULL; pxr.hi=xhi^0x3f3dfb40a54febd5ULL; n+=Ng_dpInit(pool,base,dpn,&pxr,amp,-3,4,-1); }
    else if (nptype == 0){ const double amp[]={1.5,0,1,0,0,0};
        pxr.lo=xlo^0x5c7e6b29735f0d7fULL; pxr.hi=xhi^0xf7d86f1bbc734988ULL; n+=Ng_dpInit(pool,base,dpn,&pxr,amp,-10,6,-1); }
    else if (nptype == 1){ const double amp[]={1,1,0,0,0,0};
        pxr.lo=xlo^0x81bb4d22e8dc168eULL; pxr.hi=xhi^0xf1c8b4bea16303cdULL; n+=Ng_dpInit(pool,base,dpn,&pxr,amp,-8,6,-1); }
    else if (nptype == 2){ const double amp[]={1,1,2,2,2,1,1,1,1};
        pxr.lo=xlo^0x83886c9d0ae3a662ULL; pxr.hi=xhi^0xafa638a61b42e8adULL; n+=Ng_dpInit(pool,base,dpn,&pxr,amp,-9,9,-1); }
    else if (nptype == 3){ const double amp[]={1,1,0,1,1};
        pxr.lo=xlo^0xd02491e6058f6fd8ULL; pxr.hi=xhi^0x4792512c94c17a80ULL; n+=Ng_dpInit(pool,base,dpn,&pxr,amp,-9,5,-1); }
    else { const double amp[]={1,2,1,0,0,0}; /* nptype==5 weirdness */
        pxr.lo=xlo^0xefc8ef4d36102b34ULL; pxr.hi=xhi^0x1beeeb324a0f24eaULL; n+=Ng_dpInit(pool,base,dpn,&pxr,amp,-7,6,-1); }
    return n;
}
GPUDEV static void Ng_setBiomeSeed(BiomeNoiseG *bn, uint64_t seed){
    XoroG pxr; Xg_set(&pxr, seed);
    uint64_t xlo = Xg_long(&pxr), xhi = Xg_long(&pxr);
    int n = 0;
    for (int i = 0; i < 6; i++)
        n += Ng_climate(bn->oct, n, &bn->climate[i], xlo, xhi, i);
}

// ----------------------------------------------------------------------------
// PARALLEL init: split the cheap RNG derivation (thread 0) from the expensive
// per-octave perlinInit (256-shuffle), so a whole block can do the 46 shuffles
// in parallel. Bit-identical to Ng_setBiomeSeed (validated). NG_OCT_TOTAL == 46
// (the oct[] pool is fully used for the 1.18+ normal world).
// ----------------------------------------------------------------------------
#define NG_OCT_TOTAL 46
typedef struct { uint64_t lo, hi; double amp, lac; } OctSeedG;

GPUDEV static int Ng_derOct(OctSeedG *os, int base, OctaveG *o, XoroG *xr,
                            const double *amp, int omin, int len, int nmax){
    double lacuna = NG_LAC[-omin], persist = NG_PER[len];
    uint64_t xlo = Xg_long(xr), xhi = Xg_long(xr);
    int i = 0, n = 0;
    for (; i < len && n != nmax; i++, lacuna *= 2.0, persist *= 0.5){
        if (amp[i] == 0) continue;
        os[base + n].lo = xlo ^ NG_MD5OCT[12 + omin + i][0];
        os[base + n].hi = xhi ^ NG_MD5OCT[12 + omin + i][1];
        os[base + n].amp = amp[i] * persist;
        os[base + n].lac = lacuna;
        n++;
    }
    o->oct_off = base; o->octcnt = n;
    return n;
}
GPUDEV static int Ng_derDp(OctSeedG *os, int base, DPerlinG *dp, XoroG *xr,
                           const double *amp, int omin, int len, int nmax){
    int i, n = 0, na = -1, nb = -1;
    if (nmax > 0){ na = (nmax + 1) >> 1; nb = nmax - na; }
    n += Ng_derOct(os, base + n, &dp->octA, xr, amp, omin, len, na);
    n += Ng_derOct(os, base + n, &dp->octB, xr, amp, omin, len, nb);
    for (i = len - 1; i >= 0 && amp[i] == 0.0; i--) len--;
    for (i = 0; amp[i] == 0.0; i++) len--;
    dp->amplitude = NG_AMPI[len];
    return n;
}
// nmax: octave limit of the parameter ("up to octave" = cubiomes' nmax; -1 = all),
// split between the two perlins exactly like xDoublePerlinInit / Ng_dpInit
GPUDEV static int Ng_derClimate(OctSeedG *os, int base, DPerlinG *dpn,
                                uint64_t xlo, uint64_t xhi, int nptype, int nmax){
    XoroG pxr; int n = 0;
    if (nptype == 4){ const double amp[]={1,1,1,0};
        pxr.lo=xlo^0x080518cf6af25384ULL; pxr.hi=xhi^0x3f3dfb40a54febd5ULL; n+=Ng_derDp(os,base,dpn,&pxr,amp,-3,4,nmax); }
    else if (nptype == 0){ const double amp[]={1.5,0,1,0,0,0};
        pxr.lo=xlo^0x5c7e6b29735f0d7fULL; pxr.hi=xhi^0xf7d86f1bbc734988ULL; n+=Ng_derDp(os,base,dpn,&pxr,amp,-10,6,nmax); }
    else if (nptype == 1){ const double amp[]={1,1,0,0,0,0};
        pxr.lo=xlo^0x81bb4d22e8dc168eULL; pxr.hi=xhi^0xf1c8b4bea16303cdULL; n+=Ng_derDp(os,base,dpn,&pxr,amp,-8,6,nmax); }
    else if (nptype == 2){ const double amp[]={1,1,2,2,2,1,1,1,1};
        pxr.lo=xlo^0x83886c9d0ae3a662ULL; pxr.hi=xhi^0xafa638a61b42e8adULL; n+=Ng_derDp(os,base,dpn,&pxr,amp,-9,9,nmax); }
    else if (nptype == 3){ const double amp[]={1,1,0,1,1};
        pxr.lo=xlo^0xd02491e6058f6fd8ULL; pxr.hi=xhi^0x4792512c94c17a80ULL; n+=Ng_derDp(os,base,dpn,&pxr,amp,-9,5,nmax); }
    else { const double amp[]={1,2,1,0,0,0};
        pxr.lo=xlo^0xefc8ef4d36102b34ULL; pxr.hi=xhi^0x1beeeb324a0f24eaULL; n+=Ng_derDp(os,base,dpn,&pxr,amp,-7,6,nmax); }
    return n;
}
// thread-0 step: derive all 46 octave seeds + set climate offsets. Returns count (46).
GPUDEV static int Ng_deriveOctaves(BiomeNoiseG *bn, uint64_t seed, OctSeedG *os){
    XoroG pxr; Xg_set(&pxr, seed);
    uint64_t xlo = Xg_long(&pxr), xhi = Xg_long(&pxr);
    int n = 0;
    for (int i = 0; i < 6; i++)
        n += Ng_derClimate(os, n, &bn->climate[i], xlo, xhi, i, -1);
    return n;
}
// per-octave step (run in parallel): the expensive 256-shuffle from a derived seed.
GPUDEV static void Ng_perlinInitFrom(PerlinG *p, const OctSeedG *os){
    XoroG xr; xr.lo = os->lo; xr.hi = os->hi;
    p->a = Xg_dbl(&xr) * 256.0; p->b = Xg_dbl(&xr) * 256.0; p->c = Xg_dbl(&xr) * 256.0;
    p->amplitude = os->amp; p->lacunarity = os->lac;
    uint8_t *idx = p->d;
    for (int i = 0; i < 256; i++) idx[i] = (uint8_t)i;
    for (int i = 0; i < 256; i++){
        int j = Xg_int(&xr, 256 - i) + i;
        uint8_t t = idx[i]; idx[i] = idx[j]; idx[j] = t;
    }
    idx[256] = idx[0];
    double i2 = floor(p->b), d2 = p->b - i2;
    p->h2 = (uint8_t)(int)i2; p->d2 = d2;
    p->t2 = d2*d2*d2*(d2*(d2*6.0-15.0)+10.0);
}

// ----------------------------------------------------------------------------
// FP32 noise sampler  (floor/index in double = exact, interpolation in float)
// ----------------------------------------------------------------------------
GPUDEV static inline float Ng_ilf(uint8_t idx, float a, float b, float c){
    switch (idx & 0xf){
    case 0:return a+b; case 1:return -a+b; case 2:return a-b; case 3:return -a-b;
    case 4:return a+c; case 5:return -a+c; case 6:return a-c; case 7:return -a-c;
    case 8:return b+c; case 9:return -b+c; case 10:return b-c; case 11:return -b-c;
    case 12:return a+b; case 13:return -b+c; case 14:return -a+b; default:return -b-c; }
}
GPUDEV static inline float Ng_lf(float t, float a, float b){ return a + t*(b-a); }
GPUDEV static float Ng_perlin32(const PerlinG *p, double d1, double d2, double d3){
    uint8_t h1,h2,h3; float t1,t2,t3,f1,f2,f3;
    if (d2 == 0.0){ f2=(float)p->d2; h2=p->h2; t2=(float)p->t2; }
    else { d2+=p->b; double i2=floor(d2); f2=(float)(d2-i2); h2=(uint8_t)(int)i2; t2=f2*f2*f2*(f2*(f2*6-15)+10); }
    d1+=p->a; d3+=p->c; double i1=floor(d1), i3=floor(d3);
    f1=(float)(d1-i1); f3=(float)(d3-i3); h1=(uint8_t)(int)i1; h3=(uint8_t)(int)i3;
    t1=f1*f1*f1*(f1*(f1*6-15)+10); t3=f3*f3*f3*(f3*(f3*6-15)+10);
    const uint8_t *idx=p->d;
    uint8_t a1=idx[h1]+h2,b1=idx[h1+1]+h2,a2=idx[a1]+h3,b2=idx[b1]+h3,a3=idx[a1+1]+h3,b3=idx[b1+1]+h3;
    float l1=Ng_ilf(idx[a2],f1,f2,f3),   l2=Ng_ilf(idx[b2],f1-1,f2,f3);
    float l3=Ng_ilf(idx[a3],f1,f2-1,f3), l4=Ng_ilf(idx[b3],f1-1,f2-1,f3);
    float l5=Ng_ilf(idx[a2+1],f1,f2,f3-1),   l6=Ng_ilf(idx[b2+1],f1-1,f2,f3-1);
    float l7=Ng_ilf(idx[a3+1],f1,f2-1,f3-1), l8=Ng_ilf(idx[b3+1],f1-1,f2-1,f3-1);
    l1=Ng_lf(t1,l1,l2); l3=Ng_lf(t1,l3,l4); l5=Ng_lf(t1,l5,l6); l7=Ng_lf(t1,l7,l8);
    l1=Ng_lf(t2,l1,l3); l5=Ng_lf(t2,l5,l7);
    return Ng_lf(t3,l1,l5);
}
GPUDEV static float Ng_oct32(const PerlinG *pool, const OctaveG *o, double x, double y, double z){
    float v = 0;
    for (int i = 0; i < o->octcnt; i++){
        const PerlinG *p = &pool[o->oct_off + i]; double L = p->lacunarity;
        v += (float)p->amplitude * Ng_perlin32(p, x*L, y*L, z*L);
    }
    return v;
}
GPUDEV static float Ng_dp32(const PerlinG *pool, const DPerlinG *n, double x, double y, double z){
    const double f = 337.0/331.0;
    return (Ng_oct32(pool, &n->octA, x, y, z) + Ng_oct32(pool, &n->octB, x*f, y*f, z*f)) * (float)n->amplitude;
}

// ----------------------------------------------------------------------------
// flattened spline eval (biomenoise.c getSpline)
// ----------------------------------------------------------------------------
GPUDEV static float Ng_getSpline(const SplineFlatG *A, int si, const float *vals){
    const SplineFlatG *sp = &A[si];
    if (sp->len == 1) return sp->fixval;
    float f = vals[sp->typ]; int i;
    for (i = 0; i < sp->len; i++) if (sp->loc[i] >= f) break;
    if (i == 0 || i == sp->len){
        if (i) i--;
        float v = Ng_getSpline(A, sp->val[i], vals);
        return v + sp->der[i] * (f - sp->loc[i]);
    }
    int sp1=sp->val[i-1], sp2=sp->val[i];
    float g=sp->loc[i-1], h=sp->loc[i], k=(f-g)/(h-g), l=sp->der[i-1], m=sp->der[i];
    float n=Ng_getSpline(A,sp1,vals), o=Ng_getSpline(A,sp2,vals);
    float pp=l*(h-g)-(o-n), q=-m*(h-g)+(o-n);
    return (n + k*(o-n)) + k*(1.0F-k)*((pp) + k*(q-pp));
}

// ----------------------------------------------------------------------------
// climate -> biome (biomenoise.c, integer parameter tree, dat==NULL path)
// ----------------------------------------------------------------------------
GPUDEV static uint64_t Ng_npdist(const int64_t np[6], const BiomeTreeG *bt, int idx){
    uint64_t ds = 0, node = bt->nodes[idx];
    for (uint32_t i = 0; i < 6; i++){
        int ii = (node >> (8*i)) & 0xFF;
        uint64_t a = (uint64_t)(np[i] - bt->param[2*ii + 1]);
        uint64_t b = (uint64_t)((int64_t)bt->param[2*ii + 0] - np[i]);
        uint64_t d = (int64_t)a > 0 ? a : ((int64_t)b > 0 ? b : 0);
        ds += d * d;
    }
    return ds;
}
GPUDEV static int Ng_resnode(const int64_t np[6], const BiomeTreeG *bt, int idx,
                             int alt, uint64_t ds, int depth){
    if (bt->steps[depth] == 0) return idx;
    uint32_t step;
    do { step = bt->steps[depth]; depth++; } while ((uint32_t)(idx + step) >= bt->len);
    uint64_t node = bt->nodes[idx];
    int inner = node >> 48;
    int leaf = alt;
    for (uint32_t i = 0, n = bt->order; i < n; i++){
        uint64_t dsi = Ng_npdist(np, bt, inner);
        if (dsi < ds){
            int leaf2 = Ng_resnode(np, bt, inner, leaf, ds, depth);
            uint64_t dl2 = (inner == leaf2) ? dsi : Ng_npdist(np, bt, leaf2);
            if (dl2 < ds){ ds = dl2; leaf = leaf2; }
        }
        inner += step;
        if ((uint32_t)inner >= bt->len) break;
    }
    return leaf;
}
GPUDEV static int Ng_climateToBiome(const BiomeTreeG *bt, const int64_t np[6]){
    int idx = Ng_resnode(np, bt, 0, 0, (uint64_t)-1, 0);
    return (bt->nodes[idx] >> 48) & 0xFF;
}

// ----------------------------------------------------------------------------
// assembled biome at scale-4 (x,z) — FP32 path. y is the scale-4 y (15 = y60).
// ----------------------------------------------------------------------------
GPUDEV static int Ng_biomeFP32(const BiomeNoiseG *bn, const SplineFlatG *sp,
                               const BiomeTreeG *bt, int x, int y, int z){
    const PerlinG *pool = bn->oct;
    double px = x + Ng_dp32(pool, &bn->climate[4], x, 0, z) * 4.0;   // NP_SHIFT
    double pz = z + Ng_dp32(pool, &bn->climate[4], z, x, 0) * 4.0;
    float c = Ng_dp32(pool, &bn->climate[2], px, 0, pz);            // CONT
    float e = Ng_dp32(pool, &bn->climate[3], px, 0, pz);            // EROS
    float w = Ng_dp32(pool, &bn->climate[5], px, 0, pz);            // WEIRD
    float npp[4] = { c, e, -3.0F*(fabsf(fabsf(w)-0.6666667F)-0.33333334F), w };
    double off = Ng_getSpline(sp, 0, npp) + 0.015F;
    float d = 1.0 - (y*4)/128.0 - 83.0/160.0 + off;
    float t = Ng_dp32(pool, &bn->climate[0], px, 0, pz);           // TEMP
    float h = Ng_dp32(pool, &bn->climate[1], px, 0, pz);           // HUMID
    int64_t np[6] = {
        (int64_t)(10000.0F*t), (int64_t)(10000.0F*h), (int64_t)(10000.0F*c),
        (int64_t)(10000.0F*e), (int64_t)(10000.0F*d), (int64_t)(10000.0F*w)
    };
    return Ng_climateToBiome(bt, np);
}

// ----------------------------------------------------------------------------
// per-seed BiomeScan prefilter: count grid samples whose biome is in `allowed`
// (256-bit bitmask: word b>>6, bit b&63). Condition-agnostic — the host builds
// the sample grid + bitmask from the condition, the device just counts. `bn` is
// per-thread (global) or per-block (shared) scratch.
// ----------------------------------------------------------------------------
GPUDEV static int Ng_scanSeed(BiomeNoiseG *bn, const SplineFlatG *sp, const BiomeTreeG *bt,
                              uint64_t seed, const int *xs, const int *zs, int nsamp,
                              int y, const uint64_t allowed[4]){
    Ng_setBiomeSeed(bn, seed);
    int cnt = 0;
    for (int i = 0; i < nsamp; i++){
        int b = Ng_biomeFP32(bn, sp, bt, xs[i], y, zs[i]);
        if (b >= 0 && b < 256 && (allowed[(b >> 6) & 3] & (1ULL << (b & 63)))) cnt++;
    }
    return cnt;
}

#endif // NOISE_GPU_KERNEL_CUH
