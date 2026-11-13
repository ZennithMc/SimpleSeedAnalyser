/*
 * structcrack_core.h — (06/11, SSA) structures seen → structure seed (48 bits).
 *
 * Every "random spread" structure (villages, temples, shipwrecks, monuments, …) is
 * tried once per region of `region` chunks: Random(ss + rx·341873128712 +
 * rz·132897987541 + salt) draws its chunk in the region (nextInt(range) twice, or
 * the mean of two draws for the large structures). Same for every version from
 * 1.13 to 26.x (the 64-bit world seed only decides the biomes).
 *
 * Lifting (SeedCrackerX's method): when range = 2^k·m, nextInt(range) mod 2^k only
 * depends on the 17 + k low bits of the seed → the 20 low bits are filtered by every
 * such structure first, then the 28 high bits are searched for the survivors.
 */
#pragma once
#include <stdint.h>

#ifdef __CUDACC__
#define SC_FN __host__ __device__ static inline
#else
#define SC_FN static inline
#endif

#define SC_MAXOBS  48
#define SC_MAXCAND 25
#define SC_LOWBITS 20

enum {
    SCK_FEATURE = 0,        // linear: x = nextInt(r), z = nextInt(r)
    SCK_LARGE = 1,          // triangular: (nextInt(r) + nextInt(r)) / 2  (monument, mansion, end city)
    SCK_OUTPOST = 2,        // feature + attempt seed: nextInt(5) == 0
    SCK_FORTRESS16 = 3,     // 1.16 – 1.17 nether: feature then nextInt(5) < 2
    SCK_BASTION16 = 4,      // 1.16 – 1.17 nether: feature then nextInt(5) >= 2
    SCK_BASTION18 = 5,      // 1.18+: feature + chunkGenerateRnd: nextInt(5) >= 2
    SCK_ENDCITY = 6,        // large, and at least 1008 blocks from 0, 0
};

typedef struct {
    int32_t kind;
    int32_t region, range;
    int32_t ncand;
    int64_t salt;
    int32_t rx[SC_MAXCAND], rz[SC_MAXCAND];      // region of each candidate chunk
    int32_t ox[SC_MAXCAND], oz[SC_MAXCAND];      // its chunk inside the region
} ScObs;

typedef struct {
    int32_t n;
    ScObs o[SC_MAXOBS];
} ScSet;

#define SC_A 0x5DEECE66DULL
#define SC_B 0xBULL
#define SC_M ((1ULL << 48) - 1)

SC_FN uint64_t sc_step(uint64_t s) { return (s * SC_A + SC_B) & SC_M; }

SC_FN int32_t sc_int(uint64_t *s, int32_t n) {
    *s = sc_step(*s);
    uint32_t bits = (uint32_t)(*s >> 17);
    if ((n & -n) == n)
        return (int32_t)(((uint64_t)n * bits) >> 31);
    return (int32_t)(bits % (uint32_t)n);          // (the rejection loop: < n / 2^31, ignored)
}

SC_FN int64_t sc_long(uint64_t *s) {
    *s = sc_step(*s);
    int64_t hi = (int32_t)(uint32_t)(*s >> 16);
    *s = sc_step(*s);
    int64_t lo = (int32_t)(uint32_t)(*s >> 16);
    return (int64_t)((uint64_t)hi << 32) + lo;
}

SC_FN uint64_t sc_region_seed(uint64_t ss, int32_t rx, int32_t rz, int64_t salt) {
    uint64_t s = ss + (uint64_t)(int64_t)rx * 341873128712ULL + (uint64_t)(int64_t)rz * 132897987541ULL
                 + (uint64_t)salt;
    return (s ^ SC_A) & SC_M;
}

/* does structure seed ss put this observation's structure in candidate c? */
SC_FN int sc_cand(const ScObs *o, int c, uint64_t ss) {
    uint64_t s = sc_region_seed(ss, o->rx[c], o->rz[c], o->salt);
    int32_t r = o->range, x, z;
    if (o->kind == SCK_LARGE || o->kind == SCK_ENDCITY) {
        x = sc_int(&s, r); x += sc_int(&s, r); x >>= 1;
        if (x != o->ox[c]) return 0;
        z = sc_int(&s, r); z += sc_int(&s, r); z >>= 1;
        if (z != o->oz[c]) return 0;
        if (o->kind == SCK_ENDCITY) {
            int64_t bx = ((int64_t)o->rx[c] * o->region + x) * 16, bz = ((int64_t)o->rz[c] * o->region + z) * 16;
            if (bx * bx + bz * bz < 1008LL * 1008LL) return 0;
        }
        return 1;
    }
    x = sc_int(&s, r);
    if (x != o->ox[c]) return 0;
    z = sc_int(&s, r);
    if (z != o->oz[c]) return 0;
    if (o->kind == SCK_FORTRESS16)
        return sc_int(&s, 5) < 2;
    if (o->kind == SCK_BASTION16)
        return sc_int(&s, 5) >= 2;
    int32_t cx = o->rx[c] * o->region + x, cz = o->rz[c] * o->region + z;
    if (o->kind == SCK_OUTPOST) {
        uint64_t t = ss ^ ((uint64_t)(int64_t)(cx >> 4) ^ ((uint64_t)(int64_t)(cz >> 4) << 4));
        t = (t ^ SC_A) & SC_M;
        t = sc_step(t);                              // next(31)
        return sc_int(&t, 5) == 0;
    }
    if (o->kind == SCK_BASTION18) {
        uint64_t t = (ss ^ SC_A) & SC_M;
        int64_t a = sc_long(&t), b = sc_long(&t);
        uint64_t v = ((uint64_t)a * (uint64_t)(int64_t)cx) ^ ((uint64_t)b * (uint64_t)(int64_t)cz) ^ ss;
        t = (v ^ SC_A) & SC_M;
        return sc_int(&t, 5) >= 2;
    }
    return 1;
}

SC_FN int sc_obs(const ScObs *o, uint64_t ss) {
    for (int c = 0; c < o->ncand; c++)
        if (sc_cand(o, c, ss)) return 1;
    return 0;
}

SC_FN int sc_test(const ScSet *S, uint64_t ss) {
    for (int i = 0; i < S->n; i++)
        if (!sc_obs(&S->o[i], ss)) return 0;
    return 1;
}

/* how many low bits of x / z an observation can check from the 20 low bits */
SC_FN int sc_lift_k(const ScObs *o) {
    if (o->kind == SCK_LARGE || o->kind == SCK_ENDCITY) return 0;
    int r = o->range;
    if ((r & -r) == r) return 0;                     // powers of two use the high bits
    int k = 0;
    while (!(r & 1)) { r >>= 1; k++; }
    return k > SC_LOWBITS - 17 ? SC_LOWBITS - 17 : k;
}

/* the 20 low bits of ss agree with this observation (some candidate) */
SC_FN int sc_lift_obs(const ScObs *o, uint32_t low) {
    int k = sc_lift_k(o);
    if (!k) return 1;
    const uint64_t ML = (1ULL << SC_LOWBITS) - 1;
    const uint32_t mk = (1u << k) - 1;
    for (int c = 0; c < o->ncand; c++) {
        uint64_t s = sc_region_seed(low, o->rx[c], o->rz[c], o->salt) & ML;
        s = (s * SC_A + SC_B) & ML;
        if ((((uint32_t)(s >> 17)) & mk) != ((uint32_t)o->ox[c] & mk)) continue;
        s = (s * SC_A + SC_B) & ML;
        if ((((uint32_t)(s >> 17)) & mk) != ((uint32_t)o->oz[c] & mk)) continue;
        return 1;
    }
    return 0;
}

SC_FN int sc_lift_low(const ScSet *S, uint32_t low) {
    for (int i = 0; i < S->n; i++)
        if (!sc_lift_obs(&S->o[i], low)) return 0;
    return 1;
}
