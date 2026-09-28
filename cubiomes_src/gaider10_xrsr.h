#ifndef XRSR_H
#define XRSR_H
#include <stdint.h>

#define XRSR_MIX1 0xbf58476d1ce4e5b9ULL
#define XRSR_MIX2 0x94d049bb133111ebULL
#define XRSR_MIX1_INVERSE 0x96de1b173f119089ULL
#define XRSR_MIX2_INVERSE 0x319642b2d24d8ec3ULL
#define XRSR_SILVER_RATIO 0x6a09e667f3bcc909ULL
#define XRSR_GOLDEN_RATIO 0x9e3779b97f4a7c15ULL

#ifdef __CUDACC__
#define XRSRDEV __host__ __device__
#else
#define XRSRDEV
#endif

typedef struct xrsr128 XRSR128;
typedef struct xrsrmat XRSRMAT;

struct xrsr128 {
	uint64_t lo;
	uint64_t hi;
};

struct xrsrmat {
	XRSR128 elem[128];
};

XRSRDEV void xrsr128_init(XRSR128 *rng, uint64_t lo, uint64_t hi);
XRSRDEV void xrsr128_next(XRSR128 *rng);
XRSRDEV void xrsr128_prev(XRSR128 *rng);
XRSRDEV void xrsr128_comb(XRSR128 *rng, XRSRMAT *other);
void xrsr128_skip(XRSR128 *rng, XRSR128 *other);

XRSRDEV void xrsrmat_init(XRSRMAT *mat);
XRSRDEV void xrsrmat_next(XRSRMAT *mat);
XRSRDEV void xrsrmat_prev(XRSRMAT *mat);
XRSRDEV void xrsrmat_comb(XRSRMAT *mat, XRSRMAT *other);
void xrsrmat_skip(XRSRMAT *mat, XRSR128 *other);

void xrsr_init(void);
XRSRDEV void xrsr_seed(XRSR128 *rng, uint64_t seed);
XRSRDEV uint64_t xrsr_long(XRSR128 *rng);
XRSRDEV uint64_t xrsr_lo2s(uint64_t lo);
XRSRDEV uint64_t xrsr_hi2s(uint64_t hi);

#ifdef __CUDACC__
#include "xrsr.c"
#undef XRSRDEV
#endif

#endif
