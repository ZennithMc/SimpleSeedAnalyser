#include "xrsr.h"

static XRSRMAT skips[128];

XRSRDEV static uint64_t mix64(uint64_t u64)
{
	u64 = (u64 ^ u64 >> 30) * XRSR_MIX1;
	u64 = (u64 ^ u64 >> 27) * XRSR_MIX2;
	return u64 ^ u64 >> 31;
}

XRSRDEV static uint64_t fix64(uint64_t u64)
{
	u64 = (u64 ^ u64 >> 31 ^ u64 >> 62) * XRSR_MIX2_INVERSE;
	u64 = (u64 ^ u64 >> 27 ^ u64 >> 54) * XRSR_MIX1_INVERSE;
	return u64 ^ u64 >> 30 ^ u64 >> 60;
}

XRSRDEV static uint64_t rol64(uint64_t u64, int n)
{
	return u64 << n | u64 >> (64 - n);
}

XRSRDEV void xrsr128_init(XRSR128 *rng, uint64_t lo, uint64_t hi)
{
	rng->lo = lo;
	rng->hi = hi;
}

XRSRDEV void xrsr128_next(XRSR128 *rng)
{
	rng->hi = rng->hi ^ rng->lo;
	rng->lo = rol64(rng->lo, 49) ^ rng->hi ^ (rng->hi << 21);
	rng->hi = rol64(rng->hi, 28);
}

XRSRDEV void xrsr128_prev(XRSR128 *rng)
{
	rng->hi = rol64(rng->hi, 36);
	rng->lo = rol64(rng->lo ^ rng->hi ^ (rng->hi << 21), 15);
	rng->hi = rng->hi ^ rng->lo;
}

XRSRDEV void xrsr128_comb(XRSR128 *rng, XRSRMAT *other)
{
	XRSR128 tmp = *rng;
	xrsr128_init(rng, 0, 0);
	for (int i = 0; i < 64; i++) {
		if (tmp.lo & (uint64_t) 1 << i) {
			rng->hi ^= other->elem[i].hi;
			rng->lo ^= other->elem[i].lo;
		}
	}
	for (int j = 0; j < 64; j++) {
		if (tmp.hi & (uint64_t) 1 << j) {
			rng->hi ^= other->elem[j + 64].hi;
			rng->lo ^= other->elem[j + 64].lo;
		}
	}
}

void xrsr128_skip(XRSR128 *rng, XRSR128 *other)
{
	for (int i = 0; i < 64; i++)
		if (other->lo & (uint64_t) 1 << i)
			xrsr128_comb(rng, &skips[i]);
	for (int j = 0; j < 64; j++)
		if (other->hi & (uint64_t) 1 << j)
			xrsr128_comb(rng, &skips[j + 64]);
}

XRSRDEV void xrsrmat_init(XRSRMAT *mat)
{
	for (int i = 0; i < 64; i++)
		xrsr128_init(&mat->elem[i], (uint64_t) 1 << i, 0);
	for (int j = 0; j < 64; j++)
		xrsr128_init(&mat->elem[j + 64], 0, (uint64_t) 1 << j);
}

XRSRDEV void xrsrmat_next(XRSRMAT *mat)
{
	for (int i = 0; i < 128; i++)
		xrsr128_next(&mat->elem[i]);
}

XRSRDEV void xrsrmat_prev(XRSRMAT *mat)
{
	for (int i = 0; i < 128; i++)
		xrsr128_prev(&mat->elem[i]);
}

XRSRDEV void xrsrmat_comb(XRSRMAT *mat, XRSRMAT *other)
{
	for (int i = 0; i < 128; i++)
		xrsr128_comb(&mat->elem[i], other);
}

void xrsrmat_skip(XRSRMAT *mat, XRSR128 *other)
{
	for (int i = 0; i < 128; i++)
		xrsr128_skip(&mat->elem[i], other);
}

void xrsr_init(void)
{
	xrsrmat_init(&skips[0]);
	xrsrmat_next(&skips[0]);
	for (int i = 1; i < 128; i++) {
		skips[i] = skips[i - 1];
		xrsrmat_comb(&skips[i], &skips[i - 1]);
	}
}

XRSRDEV void xrsr_seed(XRSR128 *rng, uint64_t seed)
{
	seed ^= XRSR_SILVER_RATIO;
	rng->lo = mix64(seed);
	rng->hi = mix64(seed + XRSR_GOLDEN_RATIO);
}

XRSRDEV uint64_t xrsr_seed_lo(uint64_t seed)
{
	return mix64(seed ^ XRSR_SILVER_RATIO);
}

XRSRDEV uint64_t xrsr_seed_hi(uint64_t seed)
{
	return mix64((seed ^ XRSR_SILVER_RATIO) + XRSR_GOLDEN_RATIO);
}

XRSRDEV uint64_t xrsr_long(XRSR128 *rng)
{
	uint64_t res = rol64(rng->lo + rng->hi, 17) + rng->lo;
	xrsr128_next(rng);
	return res;
}

XRSRDEV uint64_t xrsr_lo2s(uint64_t lo)
{
	return XRSR_SILVER_RATIO ^ fix64(lo);
}

XRSRDEV uint64_t xrsr_hi2s(uint64_t hi)
{
	return XRSR_SILVER_RATIO ^ fix64(hi) - XRSR_GOLDEN_RATIO;
}
