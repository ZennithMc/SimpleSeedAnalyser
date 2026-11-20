/*
 * loot_reverse.c — (15/11) the loot finder's Reverse mode (Java 1.16 – 1.17).
 *
 * A chest's loot is LootTable.fill on java.util.Random seeded with the chest's
 * loot seed: 2^48 possible chests per table. Instead of testing world seeds one by
 * one, the Reverse mode asks which RNG states give the wanted loot, then which
 * world seeds put such a chest near the centre:
 *
 *   1. lr_new / lr_next — the 48-bit states x whose draws fall in given ranges:
 *        draw t reads s_t = A_t x + C_t (mod 2^48) (A_t, C_t: the LCG jumped t steps)
 *        kind 0  s_t in [lo, hi]                   (nextInt(2^k), nextFloat, nextBoolean)
 *        kind 1  ((s_t >> 17) mod n) in [lo, hi]   (nextInt(n), n not a power of 2)
 *      The points (s_t1 … s_tm, k_i …) form a lattice (a mod draw adds k = the quotient
 *      of s >> 17 by n); the wanted states are its points in a box. LLL on the box
 *      scaled to a cube (by powers of 2), then every lattice point of the ball around
 *      the box is enumerated (Fincke–Pohst, resumable), the box checked exactly and
 *      the state recovered and checked on every constraint. Complete (up to Java's
 *      nextInt re-draws, < n / 2^31 per draw, which the caller's exact fill rejects).
 *      Constraints beyond `dmax` dimensions are checked at the leaves only.
 *
 *   2. lr_pop_scan — a chest state → the loot seed's low 48 bits → the structure
 *      RNG state before that nextLong (2^16 candidates, ~1 fits) → back `dsteps`
 *      draws → the decoration seed of the chest's chunk → the structure seeds
 *      (population seed reversal, the lifting of treecrack_core.h, with the
 *      structure's placement in its region checked on the low bits as early as
 *      they are known) whose structure starts in the given chunk.
 *
 * Compiled with loot_vm.c into lootvm (loot_native.py).
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "treecrack_core.h"

#if defined(_WIN32)
#define LR_API __declspec(dllexport)
#else
#define LR_API __attribute__((visibility("default")))
#endif

#define LR_M48 ((1ULL << 48) - 1)
#define LR_MULT 0x5DEECE66DULL
#define LR_ADD 0xBULL
#define LR_MULT_INV 0xDFE05BCB1365ULL

#define LR_MAXC 48
#define LR_MAXD 72

typedef __int128 i128;

/* (15/11) Windows starts a process (python.exe, MSVC's runtime) with the x87 unit at
   53-bit precision: the long doubles below would be plain doubles, too short for the
   reduction (the solver then lost every state and said "impossible"). Each entry
   point that computes in long double switches the unit to 64 bits and back. */
#if defined(__GNUC__) && (defined(__x86_64__) || defined(__i386__))
#define LR_FPU_BEGIN                                                              \
    unsigned short lr_cw_, lr_nw_;                                                \
    __asm__ volatile("fnstcw %0" : "=m"(lr_cw_) : : "memory");                    \
    lr_nw_ = (unsigned short)(lr_cw_ | 0x0300);                                   \
    __asm__ volatile("fldcw %0" : : "m"(lr_nw_) : "memory");
#define LR_FPU_END __asm__ volatile("fldcw %0" : : "m"(lr_cw_) : "memory");
#else
#define LR_FPU_BEGIN
#define LR_FPU_END
#endif

/* s → A s + C after `steps` draws (steps < 0: backwards) */
static void lr_jump(int64_t steps, uint64_t *A, uint64_t *C)
{
    uint64_t a = LR_MULT, c = LR_ADD;
    if (steps < 0) {                 /* the inverse step: s → MULT^-1 (s - 11) */
        a = LR_MULT_INV;
        c = (uint64_t)(-(int64_t)((LR_MULT_INV * LR_ADD) & LR_M48)) & LR_M48;
        steps = -steps;
    }
    uint64_t RA = 1, RC = 0;
    while (steps) {
        if (steps & 1) { RA = (RA * a) & LR_M48; RC = (RC * a + c) & LR_M48; }
        c = (c * a + c) & LR_M48;
        a = (a * a) & LR_M48;
        steps >>= 1;
    }
    *A = RA; *C = RC;
}

static uint64_t lr_inv48(uint64_t a)          /* a odd: a^-1 mod 2^48 */
{
    uint64_t x = a;                            /* Newton: 3 → 6 → 12 → 24 → 48 bits */
    for (int i = 0; i < 6; i++) x = (x * (2 - a * x)) & LR_M48;
    return x & LR_M48;
}

typedef struct {
    int32_t t, kind;
    int64_t n, lo, hi;
    uint64_t A, C;
    double bits;
} LrCon;

typedef struct {
    int m;
    LrCon con[LR_MAXC];
    int nl, lat[LR_MAXC];              /* the constraints in the lattice */
    int D;
    i128 B[LR_MAXD][LR_MAXD];          /* reduced basis (rows), original units */
    i128 T[LR_MAXD], lo[LR_MAXD], hi[LR_MAXD];
    long double sc[LR_MAXD];
    long double bf[LR_MAXD][LR_MAXD];  /* scaled rows */
    long double mu[LR_MAXD][LR_MAXD], Bn[LR_MAXD], om[LR_MAXD], R2;
    long double bs[LR_MAXD][LR_MAXD];  /* b*_i (scaled) */
    long double gl[LR_MAXD][LR_MAXD];  /* the unknown levels' reach on each coordinate */
    long double half[LR_MAXD], eps[LR_MAXD];
    long double K[LR_MAXD + 1][LR_MAXD];
    uint64_t A0inv, C0;
    int kind0;
    int64_t n0;
    int kcol0;
    /* enumeration */
    int lvl, started, done;
    int64_t c[LR_MAXD], cmin[LR_MAXD], cmax[LR_MAXD], zs[LR_MAXD], zk[LR_MAXD];
    int ex[LR_MAXD];
    long double ctr[LR_MAXD], part[LR_MAXD + 1];
    int64_t nodes;
    double expected, expected_lat;
} LrSolver;

static long double lr_dot(const long double *a, const long double *b, int D)
{
    long double s = 0;
    for (int i = 0; i < D; i++) s += a[i] * b[i];
    return s;
}

static void lr_row_f(LrSolver *S, int k)
{
    for (int j = 0; j < S->D; j++) S->bf[k][j] = (long double)S->B[k][j] * S->sc[j];
}

/* GSO of row k from the rows before it (mu[k][*], Bn[k]) */
static void lr_gso_row(LrSolver *S, int k, long double r[LR_MAXD])
{
    int D = S->D;
    for (int j = 0; j <= k; j++) {
        long double v = lr_dot(S->bf[k], S->bf[j], D);
        for (int l = 0; l < j; l++) v -= S->mu[j][l] * r[l];
        r[j] = v;
        if (j < k) S->mu[k][j] = v / S->Bn[j];
    }
    S->Bn[k] = r[k];
}

static i128 lr_round128(long double v)
{
    v = roundl(v);
    if (fabsl(v) < 9.0e18L) return (i128)(int64_t)v;
    int e;
    long double m = frexpl(v, &e);           /* v = m 2^e, 0.5 <= |m| < 1 */
    int64_t top = (int64_t)ldexpl(m, 62);
    return (i128)top << (e - 62);
}

static void lr_lll(LrSolver *S)
{
    int D = S->D;
    long double r[LR_MAXD];
    const long double delta = 0.99L;
    for (int i = 0; i < D; i++) lr_row_f(S, i);
    S->Bn[0] = lr_dot(S->bf[0], S->bf[0], D);
    int k = 1, guard = 0;
    while (k < D && guard++ < 2000000) {
        if (k == 1) S->Bn[0] = lr_dot(S->bf[0], S->bf[0], D);
        lr_gso_row(S, k, r);
        for (int pass = 0; pass < 8; pass++) {        /* size reduction (again if q was big) */
            int big = 0, any = 0;
            for (int j = k - 1; j >= 0; j--) {
                long double m = S->mu[k][j];
                if (fabsl(m) <= 0.5L) continue;
                i128 q = lr_round128(m);
                long double qf = (long double)q;
                if (fabsl(qf) > 1.0e9L) big = 1;
                any = 1;
                for (int l = 0; l < D; l++) S->B[k][l] -= q * S->B[j][l];
                for (int l = 0; l < j; l++) S->mu[k][l] -= qf * S->mu[j][l];
                S->mu[k][j] -= qf;
            }
            if (!any) break;
            lr_row_f(S, k);
            lr_gso_row(S, k, r);
            if (!big) break;
        }
        long double m1 = S->mu[k][k - 1];
        if (S->Bn[k] < (delta - m1 * m1) * S->Bn[k - 1]) {
            for (int l = 0; l < D; l++) {
                i128 t = S->B[k][l]; S->B[k][l] = S->B[k - 1][l]; S->B[k - 1][l] = t;
                long double f = S->bf[k][l]; S->bf[k][l] = S->bf[k - 1][l]; S->bf[k - 1][l] = f;
            }
            k = k > 1 ? k - 1 : 1;
        } else {
            k++;
        }
    }
    /* the final GSO, from scratch */
    for (int i = 0; i < D; i++) lr_row_f(S, i);
    S->Bn[0] = lr_dot(S->bf[0], S->bf[0], D);
    for (int i = 1; i < D; i++) lr_gso_row(S, i, r);
}

static int lr_check(const LrCon *c, uint64_t x)
{
    uint64_t s = (c->A * x + c->C) & LR_M48;
    if (c->kind == 0) return (int64_t)s >= c->lo && (int64_t)s <= c->hi;
    int64_t v = (int64_t)(s >> 17) % c->n;
    return v >= c->lo && v <= c->hi;
}

static int lr_ilog2(uint64_t v) { int e = -1; while (v) { v >>= 1; e++; } return e; }

static int cmp_bits(const void *a, const void *b)
{
    const LrCon *x = *(const LrCon * const *)a, *y = *(const LrCon * const *)b;
    double ux = x->bits / (x->kind ? 2.0 : 1.0), uy = y->bits / (y->kind ? 2.0 : 1.0);
    return ux < uy ? 1 : ux > uy ? -1 : 0;
}

LR_API int lr_api(void) { return 1; }

/* m constraints: step[i] (the draw reads s_step), kind[i], n[i], lo[i], hi[i] (inclusive).
   dmax: the most lattice dimensions (the rest are checked at the leaves). */
static __attribute__((noinline)) LrSolver *lr_new_x(int m, const int32_t *step, const int32_t *kind,
                                                    const int64_t *n, const int64_t *lo, const int64_t *hi,
                                                    int dmax)
{
    if (m < 1 || m > LR_MAXC) return NULL;
    LrSolver *S = (LrSolver *)calloc(1, sizeof(LrSolver));
    if (!S) return NULL;
    S->m = m;
    double logE = 48.0, logEl = 48.0;
    const LrCon *ord[LR_MAXC];
    for (int i = 0; i < m; i++) {
        LrCon *c = &S->con[i];
        c->t = step[i]; c->kind = kind[i]; c->n = n[i]; c->lo = lo[i]; c->hi = hi[i];
        if (c->kind == 1 && (c->n < 2 || (c->n & (c->n - 1)) == 0)) { free(S); return NULL; }
        if (c->lo > c->hi) { c->bits = 1e9; }
        else if (c->kind == 0) c->bits = 48.0 - log2((double)(c->hi - c->lo + 1));
        else c->bits = log2((double)c->n / (double)(c->hi - c->lo + 1));
        lr_jump(c->t, &c->A, &c->C);
        logE -= c->bits;
        ord[i] = c;
    }
    S->expected = logE < -1000 ? 0.0 : pow(2.0, logE);
    for (int i = 0; i < m; i++) if (S->con[i].lo > S->con[i].hi) { S->done = 1; return S; }
    qsort(ord, (size_t)m, sizeof ord[0], cmp_bits);
    int D = 0, nl = 0;
    for (int i = 0; i < m; i++) {
        int need = ord[i]->kind ? 2 : 1;
        if (ord[i]->bits <= 0.0) continue;                 /* no information */
        if (nl > 0 && D + need > dmax) continue;
        S->lat[nl++] = (int)(ord[i] - S->con);
        D += need;
        logEl -= ord[i]->bits;
    }
    if (nl == 0) { S->lat[nl++] = 0; D = S->con[0].kind ? 2 : 1; logEl = 48.0 - S->con[0].bits; }
    S->expected_lat = logEl < -1000 ? 0.0 : pow(2.0, logEl);
    S->nl = nl; S->D = D;
    /* the basis */
    const LrCon *c0 = &S->con[S->lat[0]];
    uint64_t inv0 = lr_inv48(c0->A);
    S->A0inv = inv0; S->C0 = c0->C; S->kind0 = c0->kind; S->n0 = c0->n;
    int kc = nl;
    int kcol[LR_MAXC];
    for (int i = 0; i < nl; i++) {
        const LrCon *c = &S->con[S->lat[i]];
        kcol[i] = c->kind ? kc++ : -1;
    }
    S->kcol0 = kcol[0];
    for (int i = 0; i < nl; i++) {
        const LrCon *c = &S->con[S->lat[i]];
        S->B[0][i] = (i128)((c->A * inv0) & LR_M48);
        if (i > 0) S->B[i][i] = (i128)1 << 48;
        S->T[i] = (i128)c->C;
        if (c->kind == 0) { S->lo[i] = c->lo; S->hi[i] = c->hi; }
        else {
            S->lo[i] = (i128)c->lo << 17;
            S->hi[i] = ((i128)c->hi << 17) + ((1 << 17) - 1);
            int k = kcol[i];
            S->B[k][i] = -((i128)c->n << 17);
            S->B[k][k] = 1;
            S->T[k] = 0;
            S->lo[k] = 0;
            S->hi[k] = (i128)((1LL << 31) / c->n) - 1;       /* past it Java draws again */
        }
    }
    S->B[0][0] = 1;
    /* scale every coordinate by a power of 2: its box about 2^48 wide */
    for (int j = 0; j < D; j++) {
        uint64_t W = (uint64_t)(S->hi[j] - S->lo[j]) + 1;
        int e = 48 - lr_ilog2(W);
        if (e < 0) e = 0;
        S->sc[j] = ldexpl(1.0L, e);
    }
    lr_lll(S);
    /* the ball around the box, the target in basis coordinates */
    long double w[LR_MAXD];
    long double (*bs)[LR_MAXD] = S->bs;
    S->R2 = 0;
    for (int j = 0; j < D; j++) {
        long double half = (long double)(S->hi[j] - S->lo[j]) * 0.5L * S->sc[j];
        S->half[j] = half;
        S->eps[j] = half * 1e-7L + 2.0L;
        S->R2 += half * half;
        long double cen = ((long double)S->lo[j] + (long double)(S->hi[j] - S->lo[j]) * 0.5L
                           - (long double)S->T[j]) * S->sc[j];
        w[j] = cen;
    }
    S->R2 = S->R2 * (1.0L + 1e-9L) + 4.0L;
    for (int i = 0; i < D; i++) {
        for (int j = 0; j < D; j++) bs[i][j] = S->bf[i][j];
        for (int l = 0; l < i; l++)
            for (int j = 0; j < D; j++) bs[i][j] -= S->mu[i][l] * bs[l][j];
    }
    for (int i = D - 1; i >= 0; i--) {
        long double v = lr_dot(w, bs[i], D) / S->Bn[i];
        for (int j = i + 1; j < D; j++) v -= S->mu[j][i] * S->om[j];
        S->om[i] = v;
    }
    /* gl[l][e] = sqrt(sum_{j<l} b*_j[e]^2 / Bn_j): with |the levels below l| <= sqrt(rest),
       their part of coordinate e is at most sqrt(rest) gl[l][e] (Cauchy-Schwarz) */
    for (int e = 0; e < D; e++) {
        long double acc = 0;
        for (int l = 0; l < D; l++) {
            S->gl[l][e] = sqrtl(acc);
            acc += bs[l][e] * bs[l][e] / S->Bn[l];
        }
    }
    return S;
}

LR_API LrSolver *lr_new(int m, const int32_t *step, const int32_t *kind, const int64_t *n,
                        const int64_t *lo, const int64_t *hi, int dmax)
{
    LR_FPU_BEGIN
    LrSolver *S = lr_new_x(m, step, kind, n, lo, hi, dmax);
    LR_FPU_END
    return S;
}

LR_API double lr_expected(LrSolver *S, int lattice_only) { return lattice_only ? S->expected_lat : S->expected; }
LR_API int lr_dims(LrSolver *S) { return S->D; }
LR_API int lr_isdone(LrSolver *S) { return S->done; }
LR_API int64_t lr_nodes(LrSolver *S) { return S->nodes; }
LR_API void lr_free(LrSolver *S) { free(S); }

/* the next value of level l, from its centre outwards; 0 when none is left */
static int lr_zz(LrSolver *S, int l)
{
    int64_t lo = S->zs[l] - S->cmin[l], hi = S->cmax[l] - S->zs[l];
    int64_t reach = lo > hi ? lo : hi;
    for (;;) {
        int64_t k = S->zk[l]++;
        int64_t m = (k + 1) / 2;
        if (m > reach) { S->ex[l] = 1; return 0; }
        int64_t v = S->zs[l] + ((k & 1) ? m : -m);
        if (v >= S->cmin[l] && v <= S->cmax[l]) { S->c[l] = v; return 1; }
    }
}

static int lr_level_bounds(LrSolver *S, int l)
{
    long double s = S->om[l];
    for (int j = l + 1; j < S->D; j++) s -= S->mu[j][l] * ((long double)S->c[j] - S->om[j]);
    S->ctr[l] = s;
    long double rest = S->R2 - S->part[l + 1];
    if (rest < 0) return 0;
    long double r = sqrtl(rest / S->Bn[l]), sr = sqrtl(rest);
    long double ylo = -r, yhi = r;
    const long double *Kp = S->K[l + 1];
    for (int e = 0; e < S->D; e++) {           /* the box, coordinate by coordinate */
        long double b = S->bs[l][e];
        if (fabsl(b) < 1e-30L) continue;
        long double room = S->half[e] + S->eps[e] + sr * S->gl[l][e];
        long double u = (room - Kp[e]) / b, v = (-room - Kp[e]) / b;
        if (u > v) { long double t = u; u = v; v = t; }
        if (u > ylo) ylo = u;
        if (v < yhi) yhi = v;
        if (ylo > yhi) return 0;
    }
    long double a = ceill(s + ylo), b = floorl(s + yhi);
    if (a > 9.0e18L || b < -9.0e18L || a > b) return 0;
    S->cmin[l] = (int64_t)a;
    S->cmax[l] = (int64_t)b;
    long double z = roundl(s);                 /* the centre first, then outwards (zigzag): */
    if (z < a) z = a;                          /* the first states come at once even when */
    if (z > b) z = b;                          /* the ball is much wider than the box */
    S->zs[l] = (int64_t)z;
    S->zk[l] = 0;
    S->ex[l] = 0;
    return lr_zz(S, l);
}

/* the leaf: the exact point, the box, the state, every constraint */
static int lr_leaf(LrSolver *S, uint64_t *x_out)
{
    int D = S->D;
    i128 y0 = 0, k0 = 0;
    for (int j = 0; j < D; j++) {
        i128 v = S->T[j];
        for (int i = 0; i < D; i++) if (S->c[i]) v += (i128)S->c[i] * S->B[i][j];
        if (v < S->lo[j] || v > S->hi[j]) return 0;
        if (j == 0) y0 = v;
        if (j == S->kcol0) k0 = v;
    }
    i128 s0 = y0;
    if (S->kind0 == 1) s0 += k0 * ((i128)S->n0 << 17);
    if (s0 < 0 || s0 > (i128)LR_M48) return 0;
    uint64_t x = ((((uint64_t)s0 - S->C0) & LR_M48) * S->A0inv) & LR_M48;
    for (int i = 0; i < S->m; i++) if (!lr_check(&S->con[i], x)) return 0;
    *x_out = x;
    return 1;
}

static __attribute__((noinline)) int lr_next_x(LrSolver *S, uint64_t *out, int cap, int64_t budget)
{
    int found = 0;
    int D = S->D;
    if (S->done) return 0;
    if (!S->started) {
        S->started = 1;
        S->part[D] = 0;
        S->lvl = D - 1;
        if (!lr_level_bounds(S, D - 1)) { S->done = 1; return 0; }
    }
    int64_t n0 = S->nodes;
    while (found < cap && S->nodes - n0 < budget) {
        int l = S->lvl;
        if (S->ex[l]) {
            if (l == D - 1) { S->done = 1; break; }
            S->lvl = l + 1;
            lr_zz(S, l + 1);
            continue;
        }
        S->nodes++;
        long double d = (long double)S->c[l] - S->ctr[l];
        long double p = S->part[l + 1] + S->Bn[l] * d * d;
        if (p > S->R2) { lr_zz(S, l); continue; }
        {
            long double sr = sqrtl(S->R2 - p);
            const long double *Kp = S->K[l + 1];
            long double *Kn = S->K[l];
            int out = 0;
            for (int e = 0; e < D; e++) {
                long double v = Kp[e] + d * S->bs[l][e];
                Kn[e] = v;
                if (fabsl(v) > S->half[e] + S->eps[e] + sr * S->gl[l][e]) { out = 1; break; }
            }
            if (out) { lr_zz(S, l); continue; }
        }
        if (l == 0) {
            uint64_t x;
            if (lr_leaf(S, &x)) out[found++] = x;
            lr_zz(S, 0);
            continue;
        }
        S->part[l] = p;
        if (!lr_level_bounds(S, l - 1)) { lr_zz(S, l); continue; }
        S->lvl = l - 1;
    }
    return found;
}

/* up to `cap` more states (each once); stops after `budget` nodes. Returns how many. */
LR_API int lr_next(LrSolver *S, uint64_t *out, int cap, int64_t budget)
{
    LR_FPU_BEGIN
    int k = lr_next_x(S, out, cap, budget);
    LR_FPU_END
    return k;
}

/* the precision the x87 unit gives the long doubles here (64; 53 would be wrong) */
LR_API int lr_ld_bits(void)
{
    LR_FPU_BEGIN
    volatile long double one = 1.0L, e = 1.0L;
    int b = 0;
    while (one + e != one && b < 200) { e *= 0.5L; b++; }
    LR_FPU_END
    return b;
}

/* ── the structure side ──────────────────────────────────────────────────────── */

/* the states q before a nextLong whose value has these low 48 bits */
static int lr_prev_of_long(uint64_t v48, uint64_t *out, int cap)
{
    uint32_t lo32 = (uint32_t)v48;                               /* = s2 >> 16 */
    uint32_t want = ((uint32_t)(v48 >> 32) + (lo32 >> 31)) & 0xFFFF;   /* (s1 >> 16) & 0xFFFF */
    int n = 0;
    for (uint64_t L = 0; L < 65536; L++) {
        uint64_t s2 = ((uint64_t)lo32 << 16) | L;
        uint64_t s1 = ((s2 - LR_ADD) * LR_MULT_INV) & LR_M48;
        if (((s1 >> 16) & 0xFFFF) != want) continue;
        uint64_t q = ((s1 - LR_ADD) * LR_MULT_INV) & LR_M48;
        uint64_t t = q;
        int64_t hi = (int32_t)(uint32_t)((t = (t * LR_MULT + LR_ADD) & LR_M48) >> 16);
        int64_t lo = (int32_t)(uint32_t)((t = (t * LR_MULT + LR_ADD) & LR_M48) >> 16);
        uint64_t v = (uint64_t)((hi << 32) + lo);
        if ((v & LR_M48) != v48) continue;
        if (n < cap) out[n] = q;
        n++;
    }
    return n;
}

typedef struct {
    int kind;            /* 1: one try per region (salt, region size, range); 2: a chance per chunk */
    int64_t salt;
    int region, range;
    int32_t thr;         /* kind 2: next(24) <= thr */
    int v2;              /* kind 1: trailing zeros of range (its low bits are known early) */
} LrPlace;

static inline int64_t lr_fdiv(int64_t a, int64_t b) { int64_t q = a / b; if ((a % b) && ((a < 0) != (b < 0))) q--; return q; }

static int lr_place_ok(uint64_t S, int cx, int cz, const LrPlace *pl)
{
    if (pl->kind == 1) {
        int64_t rx = lr_fdiv(cx, pl->region), rz = lr_fdiv(cz, pl->region);
        uint64_t s = S + (uint64_t)rx * 341873128712ULL + (uint64_t)rz * 132897987541ULL + (uint64_t)pl->salt;
        s = (s ^ LR_MULT) & LR_M48;
        s = (s * LR_MULT + LR_ADD) & LR_M48;
        int r = pl->range, px, pz;
        if (r & (r - 1)) {
            px = (int)(s >> 17) % r;
            s = (s * LR_MULT + LR_ADD) & LR_M48;
            pz = (int)(s >> 17) % r;
        } else {
            px = (int)(((uint64_t)r * (s >> 17)) >> 31);
            s = (s * LR_MULT + LR_ADD) & LR_M48;
            pz = (int)(((uint64_t)r * (s >> 17)) >> 31);
        }
        return px == cx - rx * pl->region && pz == cz - rz * pl->region;
    }
    if (pl->kind == 2) {
        uint64_t s = S + (uint64_t)(int64_t)cx * 341873128712ULL + (uint64_t)(int64_t)cz * 132897987541ULL
                     + (uint64_t)pl->salt;
        s = (s ^ LR_MULT) & LR_M48;
        s = (s * LR_MULT + LR_ADD) & LR_M48;
        return (int32_t)(s >> 24) <= pl->thr;
    }
    return 1;
}

/* the population seed reversal of treecrack_core.h (tc_reverse_pop) with the
   placement's low bits checked as soon as they are known */
static int lr_reverse_placed(uint64_t P, int cx, int cz, int64_t bx, int64_t bz, const LrPlace *pl,
                             uint64_t *out, int cap)
{
    P &= TC_MASK;
    int pre = 0;
    uint64_t pk = 0, pmask = 0, wantx = 0, wantz = 0;
    if (pl->kind == 1 && pl->v2 > 0 && (pl->range & (pl->range - 1))) {
        int64_t rx = lr_fdiv(cx, pl->region), rz = lr_fdiv(cz, pl->region);
        pk = (uint64_t)rx * 341873128712ULL + (uint64_t)rz * 132897987541ULL + (uint64_t)pl->salt;
        pre = 17 + pl->v2;
        pmask = (1ULL << pl->v2) - 1;
        wantx = (uint64_t)(cx - rx * pl->region) & pmask;
        wantz = (uint64_t)(cz - rz * pl->region) & pmask;
    }
    if (bx == 0 && bz == 0) {
        if (lr_place_ok(P, cx, cz, pl)) { if (cap > 0) out[0] = P; return 1; }
        return 0;
    }
    int t = tc_ctz64((uint64_t)bx | (uint64_t)bz);
    uint64_t X = (uint64_t)bx, Z = (uint64_t)bz;
    uint64_t st_s[64 * 2 + 4];
    int st_n[64 * 2 + 4];
    int sp = 0, found = 0;
    st_s[sp] = 0; st_n[sp] = 0; sp++;
    while (sp) {
        sp--;
        uint64_t S = st_s[sp];
        int n = st_n[sp];
        int kb = n > 16 ? n - 16 : 0;
        if (kb > 32) kb = 32;
        int cb = kb + t;
        if (cb > n) cb = n;
        if (cb > 0) {
            uint64_t xs = (S ^ TC_MULT) & TC_MASK;
            uint64_t km = kb ? ((1ULL << kb) - 1) : 0;
            uint64_t a = kb ? ((((xs * TC_M2 + TC_A2) & TC_MASK) >> 16) & km) | 1 : 0;
            uint64_t b = kb ? ((((xs * TC_M4 + TC_A4) & TC_MASK) >> 16) & km) | 1 : 0;
            uint64_t cm = cb >= 64 ? ~0ULL : ((1ULL << cb) - 1);
            if ((((X * a + Z * b) ^ S) & cm) != (P & cm)) continue;
        }
        if (n == pre) {
            uint64_t nm = (1ULL << n) - 1;
            uint64_t s = ((S + pk) ^ LR_MULT) & nm;
            s = (s * LR_MULT + LR_ADD) & nm;
            if (((s >> 17) & pmask) != wantx) continue;
            s = (s * LR_MULT + LR_ADD) & nm;
            if (((s >> 17) & pmask) != wantz) continue;
        }
        if (n == 48) {
            if (tc_popseed(S, bx, bz) == P && lr_place_ok(S, cx, cz, pl)) {
                if (found < cap) out[found] = S;
                found++;
            }
            continue;
        }
        st_s[sp] = S | (1ULL << n); st_n[sp] = n + 1; sp++;
        st_s[sp] = S; st_n[sp] = n + 1; sp++;
    }
    return found;
}

/* The structure seeds that give a chest with this loot RNG state (the fill's state
   after setSeed) near the centre.
     dsteps   draws of the structure RNG before the chest's nextLong in its chunk
     salt     index + 10000 step (setFeatureSeed)
     cc       per chunk: start chunk x, z, the chest's chunk x, z
     place    kind, salt, region, range, thr (see LrPlace)
   Out: the structure seeds and the index of their chunk. Returns how many (may
   exceed cap: the rest are dropped). */
LR_API int lr_pop_scan(uint64_t loot_state, int64_t dsteps, int64_t salt, const int32_t *cc, int nc,
                       int pkind, int64_t psalt, int region, int range, int32_t thr,
                       uint64_t *out, int32_t *oi, int cap)
{
    LrPlace pl;
    pl.kind = pkind; pl.salt = psalt; pl.region = region; pl.range = range; pl.thr = thr;
    pl.v2 = 0;
    if (pkind == 1 && range > 0) { int r = range; while (!(r & 1)) { r >>= 1; pl.v2++; } }
    uint64_t q[8];
    int nq = lr_prev_of_long((loot_state ^ LR_MULT) & LR_M48, q, 8);
    if (nq > 8) nq = 8;
    uint64_t BA, BC;
    lr_jump(-dsteps, &BA, &BC);
    int found = 0;
    uint64_t tmp[16];
    for (int a = 0; a < nq; a++) {
        uint64_t init = (BA * q[a] + BC) & LR_M48;
        uint64_t P = (((init ^ LR_MULT) & LR_M48) - (uint64_t)salt) & LR_M48;
        for (int i = 0; i < nc; i++) {
            int cx = cc[4 * i], cz = cc[4 * i + 1];
            int64_t bx = (int64_t)cc[4 * i + 2] * 16, bz = (int64_t)cc[4 * i + 3] * 16;
            if (pkind == 1) {
                int64_t rx = lr_fdiv(cx, region), rz = lr_fdiv(cz, region);
                if (cx - rx * region >= range || cz - rz * region >= range) continue;
            }
            int n = lr_reverse_placed(P, cx, cz, bx, bz, &pl, tmp, 16);
            for (int k = 0; k < n && k < 16; k++) {
                if (found < cap) { out[found] = tmp[k]; oi[found] = i; }
                found++;
            }
        }
    }
    return found;
}

/* (15/11) « Whole structure »: the chests that share the chunk's structure RNG. For
   each RNG start that gives `loot_state` after `dsteps` draws, the loot states of the
   nextLongs after ds[0..n) draws: out[a * n + i]. Returns how many starts (<= cap). */
LR_API int lr_siblings(uint64_t loot_state, int64_t dsteps, const int64_t *ds, int n, uint64_t *out, int cap)
{
    uint64_t q[8];
    int nq = lr_prev_of_long((loot_state ^ LR_MULT) & LR_M48, q, 8);
    if (nq > 8) nq = 8;
    if (nq > cap) nq = cap;
    uint64_t BA, BC;
    lr_jump(-dsteps, &BA, &BC);
    for (int a = 0; a < nq; a++) {
        uint64_t init = (BA * q[a] + BC) & LR_M48;
        for (int i = 0; i < n; i++) {
            uint64_t A, C, s;
            lr_jump(ds[i], &A, &C);
            s = (A * init + C) & LR_M48;
            int64_t hi = (int32_t)(uint32_t)((s = (s * LR_MULT + LR_ADD) & LR_M48) >> 16);
            int64_t lo = (int32_t)(uint32_t)((s = (s * LR_MULT + LR_ADD) & LR_M48) >> 16);
            uint64_t v = (uint64_t)((hi << 32) + lo);
            out[a * n + i] = (v ^ LR_MULT) & LR_M48;
        }
    }
    return nq;
}

/* the loot state of the nextLong after `dsteps` draws of a structure RNG seeded with
   (decoration seed + salt) — for the tests (Python does the same) */
LR_API uint64_t lr_chest_state(uint64_t deco, int64_t salt, int64_t dsteps)
{
    uint64_t s = ((deco + (uint64_t)salt) ^ LR_MULT) & LR_M48;
    uint64_t A, C;
    lr_jump(dsteps, &A, &C);
    s = (A * s + C) & LR_M48;
    int64_t hi = (int32_t)(uint32_t)((s = (s * LR_MULT + LR_ADD) & LR_M48) >> 16);
    int64_t lo = (int32_t)(uint32_t)((s = (s * LR_MULT + LR_ADD) & LR_M48) >> 16);
    uint64_t v = (uint64_t)((hi << 32) + lo);
    return (v ^ LR_MULT) & LR_M48;
}

/* ══ (20/11) dungeons, 1.16 / 1.17 ═══════════════════════════════════════════
 * MonsterRoomFeature runs in the chunk's decorator RNG: setSeed(population seed +
 * idx + 30000) (idx 2, or 3 where a fossil comes first: desert, swamp), 8 attempts.
 * An attempt draws x z y (nextInt 16, 16, 256) and the room's half sizes (2 nextInt(2));
 * a placed room then draws one nextInt(4) per floor block over solid ground (the moss:
 * (2j+3)(2o+3) blocks, minus h where the ground below is open), 2 chests × up to 3 tries
 * (2 draws each; nextLong when a chest is placed), the mob. Checked on 715 dungeons of
 * the C engine (ssa_t116_dungeons): every chest explained; h = 0 for 94 %, ≤ 3 for 97 %;
 * a 2nd dungeon in the same chunk 0.3 % (not covered: the first one changes the draws).
 *
 * lr_dungeon_scan: a chest state → each way the draws can lead to it (attempt j, room,
 * holes h0..h1, which chest and on which try), checked forward (y in ymin..ymax, the room
 * size the draws give, the placed try next to a wall or to the first chest) → the
 * population seed → the structure seeds (no placement) of each given chunk.
 * out: seeds; oinfo: 4 per seed (chunk index, attempt, h, y). */
static inline int32_t lr_n31(uint64_t *s)
{
    *s = (*s * LR_MULT + LR_ADD) & LR_M48;
    return (int32_t)(*s >> 17);
}

static int lr_nint(uint64_t *s, int n)
{
    if ((n & -n) == n) return (int)(((int64_t)n * (int64_t)lr_n31(s)) >> 31);
    int32_t bits, val;
    do {
        bits = lr_n31(s);
        val = bits % n;
    } while ((int32_t)((uint32_t)bits - (uint32_t)val + (uint32_t)(n - 1)) < 0);
    return val;
}

static uint64_t lr_skip(uint64_t s, int64_t k)
{
    uint64_t A, C;
    lr_jump(k, &A, &C);
    return (A * s + C) & LR_M48;
}

LR_API int lr_dungeon_api(void) { return 1; }

LR_API int lr_dungeon_scan(uint64_t loot_state, const int32_t *cc, int nc, int idx, int h0, int h1,
                           int ymin, int ymax, uint64_t *out, int32_t *oinfo, int cap)
{
    static const int FM[3] = {49, 63, 81};
    LrPlace pl;
    memset(&pl, 0, sizeof pl);                         /* kind 0: no placement test */
    uint64_t q[8];
    int nq = lr_prev_of_long((loot_state ^ LR_MULT) & LR_M48, q, 8);
    if (nq > 8) nq = 8;
    int found = 0;
    uint64_t tmp[16];
    /* the chest configs: which = 0 (the 1st loop's chest, placed on try t0) or 1 (the 2nd
       loop's, on try t1, after a 1st loop placed on t0 or failed: t0 = 0) */
    for (int a = 0; a < nq; a++)
        for (int which = 0; which < 2; which++)
            for (int t0 = (which ? 0 : 1); t0 <= 3; t0++)
                for (int t1 = (which ? 1 : 0); t1 <= (which ? 3 : 0); t1++) {
                    int tail = which ? (t0 ? 2 * t0 + 2 : 6) + 2 * t1 : 2 * t0;
                    for (int j = 0; j < 8; j++)
                        for (int f = 0; f < 3; f++)
                            for (int h = h0; h <= h1; h++) {
                                int F = FM[f] - h;
                                if (F < 0) continue;
                                int64_t ds = 5 * j + 5 + F + tail;
                                uint64_t init = lr_skip(q[a], -ds);
                                uint64_t s = lr_skip(init, 5 * j);
                                lr_nint(&s, 16); lr_nint(&s, 16);
                                int y = lr_nint(&s, 256);
                                if (y < ymin || y > ymax) continue;
                                int rj = lr_nint(&s, 2) + 2, ro = lr_nint(&s, 2) + 2;
                                if ((2 * rj + 3) * (2 * ro + 3) != FM[f]) continue;
                                s = lr_skip(s, F);
                                int ok = 1, cx0 = 99, cz0 = 99;
                                /* the 1st loop */
                                int n0 = which ? (t0 ? t0 : 3) : t0;
                                for (int k = 1; k <= n0; k++) {
                                    int dx = lr_nint(&s, 2 * rj + 1) - rj, dz = lr_nint(&s, 2 * ro + 1) - ro;
                                    if (k == n0 && (which == 0 || t0)) {      /* the placed try */
                                        if (!(dx == rj || dx == -rj || dz == ro || dz == -ro)) ok = 0;
                                        cx0 = dx; cz0 = dz;
                                    }
                                }
                                if (!ok) continue;
                                if (which) {
                                    if (t0) s = lr_skip(s, 2);                  /* its nextLong */
                                    for (int k = 1; k <= t1; k++) {
                                        int dx = lr_nint(&s, 2 * rj + 1) - rj, dz = lr_nint(&s, 2 * ro + 1) - ro;
                                        if (k == t1) {
                                            int wall = dx == rj || dx == -rj || dz == ro || dz == -ro;
                                            int by = t0 && (abs(dx - cx0) + abs(dz - cz0) == 1);
                                            if (dx == cx0 && dz == cz0) ok = 0;
                                            else if (!wall && !by) ok = 0;
                                        }
                                    }
                                    if (!ok) continue;
                                }
                                if (s != q[a]) continue;               /* a re-drawn nextInt: not this path */
                                uint64_t P = (((init ^ LR_MULT) & LR_M48) - (uint64_t)(idx + 30000)) & LR_M48;
                                for (int i = 0; i < nc; i++) {
                                    int cx = cc[2 * i], cz = cc[2 * i + 1];
                                    int n = lr_reverse_placed(P, cx, cz, (int64_t)cx * 16, (int64_t)cz * 16, &pl, tmp, 16);
                                    for (int k = 0; k < n && k < 16; k++) {
                                        if (found < cap) {
                                            out[found] = tmp[k];
                                            if (oinfo) {
                                                oinfo[4 * found] = i; oinfo[4 * found + 1] = j;
                                                oinfo[4 * found + 2] = h; oinfo[4 * found + 3] = y;
                                            }
                                        }
                                        found++;
                                    }
                                }
                            }
                }
    return found;
}
