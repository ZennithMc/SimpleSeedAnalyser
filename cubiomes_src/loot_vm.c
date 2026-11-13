/*
 * loot_vm.c — (07/11) chest loot in C: LootTable.fill exactly as loot_engine.py
 * (and the game), on a program compiled from the game's tables by loot_native.py.
 *
 * Every draw of java.util.Random is taken in the same order as loot_engine.py:
 *   pools: conditions, rolls, per roll the entries' conditions then nextInt(total)
 *   (none when one entry), the entry's, the pool's and the table's functions;
 *   the max-stack splitter; getAvailableSlots (shuffle of 0..26);
 *   shuffleAndSplitItems; the shuffle of the stacks; each to the last free slot.
 * Floats as loot_engine (nextFloat() as a double k / 2^24; the enchanting bonus
 * in float32, as loot_enchant._span / _cost_round).
 *
 * Program (int32 words; offsets are word indexes; doubles are two words):
 *   word 0 : the TABLE's offset
 *   TABLE  : n_pools, pool_off...
 *   POOL   : conds_off, rolls_prov_off, n_entries, entry_off...
 *   ENTRY  : kind (0 empty, 1 item), weight, conds_off, item, fns_off, max_stack
 *   FNS    : n, fn_off...
 *   FN     : type, conds_off, params...
 *   CONDS  : n, cond_off...       (all must hold, in order, stops at the first false)
 *   COND   : 1 random_chance (double) | 2 inverted (cond_off) | 3 all_of (n, offs)
 *            | 4 any_of (n, offs)
 *   PROV   : 1 const int (v) | 2 uniform int (lo, hi) | 3 binomial (n, p double)
 *            | 4 uniform float (lo double, hi double) | 5 const float (no draw)
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#if defined(_WIN32)
#define LV_API __declspec(dllexport)
#else
#define LV_API __attribute__((visibility("default")))
#endif

#define M48 ((1ULL << 48) - 1)
#define MULT 0x5DEECE66DULL

typedef struct { uint64_t s; } JR;

static inline void jr_seed(JR *r, int64_t seed) { r->s = ((uint64_t)seed ^ MULT) & M48; }

static inline int32_t jr_next(JR *r, int bits)
{
    r->s = (r->s * MULT + 0xBULL) & M48;
    return (int32_t)(int64_t)(r->s >> (48 - bits));
}

static inline int32_t jr_int(JR *r, int32_t bound)
{
    if ((bound & -bound) == bound)
        return (int32_t)(((int64_t)bound * (int64_t)jr_next(r, 31)) >> 31);
    for (;;) {
        int32_t bits = jr_next(r, 31);
        int32_t val = bits % bound;
        if ((int64_t)bits - val + (bound - 1) < (1LL << 31))
            return val;
    }
}

static inline double jr_float(JR *r) { return (double)jr_next(r, 24) / 16777216.0; }

static inline int32_t mth_int(JR *r, int32_t lo, int32_t hi)
{
    return lo >= hi ? lo : jr_int(r, hi - lo + 1) + lo;
}

static inline double rd_double(const int32_t *P, int off)
{
    double d;
    memcpy(&d, P + off, sizeof d);
    return d;
}

/* ── providers, conditions ─────────────────────────────────────────────────── */
static int32_t prov_int(const int32_t *P, int off, JR *r)
{
    switch (P[off]) {
    case 1: return P[off + 1];
    case 2: return mth_int(r, P[off + 1], P[off + 2]);
    case 3: {
        int32_t n = P[off + 1], k = 0;
        double p = rd_double(P, off + 2);
        for (int32_t i = 0; i < n; i++)
            if (jr_float(r) < p)
                k++;
        return k;
    }
    case 4: {                                   /* a float used as an int: floor */
        double lo = rd_double(P, off + 1), hi = rd_double(P, off + 3);
        double v = lo >= hi ? lo : jr_float(r) * (hi - lo) + lo;
        return (int32_t)floor(v);
    }
    case 5: return (int32_t)floor(rd_double(P, off + 1));
    }
    return 0;
}

static void prov_float(const int32_t *P, int off, JR *r)   /* drawn, value unused */
{
    switch (P[off]) {
    case 2: case 3: prov_int(P, off, r); return;
    case 4: {
        double lo = rd_double(P, off + 1), hi = rd_double(P, off + 3);
        if (lo < hi)
            jr_float(r);
        return;
    }
    }
}

static int cond_one(const int32_t *P, int off, JR *r)
{
    switch (P[off]) {
    case 1: return jr_float(r) < rd_double(P, off + 1);
    case 2: return !cond_one(P, P[off + 1], r);
    case 3:
        for (int i = 0; i < P[off + 1]; i++)
            if (!cond_one(P, P[off + 2 + i], r))
                return 0;
        return 1;
    case 4:
        for (int i = 0; i < P[off + 1]; i++)
            if (cond_one(P, P[off + 2 + i], r))
                return 1;
        return 0;
    }
    return 0;
}

static int conds(const int32_t *P, int off, JR *r)
{
    if (off <= 0)
        return 1;
    for (int i = 0; i < P[off]; i++)
        if (!cond_one(P, P[off + 1 + i], r))
            return 0;
    return 1;
}

/* ── stacks ────────────────────────────────────────────────────────────────── */
#define MAXE 12
#define MAXS 160

typedef struct {
    int32_t item, count, potion, ne;
    int32_t ench[MAXE][2];
} Stack;

static void add_ench(Stack *st, int32_t id, int32_t lvl)
{
    if (st->ne < MAXE) {
        st->ench[st->ne][0] = id;
        st->ench[st->ne][1] = lvl;
        st->ne++;
    }
}

/* EnchantmentHelper.selectEnchantment after `lv` (loot_enchant.select_enchantment).
 * At off: ev, convert_to, cmax, n_local, ench_ids[n_local], compat[n_local*n_local],
 *         then per cost 0..cmax: n, (local, lvl, weight)*n  (offsets table first) */
static void ench_levels(const int32_t *P, int off, JR *r, Stack *st, int32_t lv)
{
    int32_t ev = P[off], cmax = P[off + 2], nl = P[off + 3];
    const int32_t *ids = P + off + 4;
    const int32_t *compat = ids + nl;
    const int32_t *cost_off = compat + nl * nl;          /* cmax + 1 offsets */
    if (ev <= 0)
        return;
    int32_t cost = lv + 1 + jr_int(r, ev / 4 + 1) + jr_int(r, ev / 4 + 1);
    double k1 = jr_float(r), k2 = jr_float(r);
    float a = (float)(k1 + k2);
    float t = (float)((double)a - 1.0);
    float span = (float)((double)t * (double)(float)0.15);
    float c = (float)cost;
    float cs = (float)((double)c * (double)span);
    float sum = (float)((double)c + (double)cs);
    float x = (float)((double)sum + 0.5);
    cost = (int32_t)floor((double)x);
    if (cost < 1)
        cost = 1;
    int32_t ac = cost > cmax ? cmax : cost;              /* compiled up to cmax (≥ every reachable cost) */
    const int32_t *A = P + cost_off[ac];
    int32_t n = A[0];
    int32_t loc[64], lvl[64], w[64];
    for (int i = 0; i < n && i < 64; i++) {
        loc[i] = A[1 + 3 * i];
        lvl[i] = A[2 + 3 * i];
        w[i] = A[3 + 3 * i];
    }
    if (n <= 0)
        return;
    int32_t last = -1;
    {
        int32_t tot = 0;
        for (int i = 0; i < n; i++) tot += w[i];
        if (tot > 0) {
            int32_t k = jr_int(r, tot);
            for (int i = 0; i < n; i++) {
                k -= w[i];
                if (k < 0) {
                    add_ench(st, ids[loc[i]], lvl[i]);
                    last = loc[i];
                    break;
                }
            }
        }
    }
    while (jr_int(r, 50) <= cost) {
        if (last >= 0) {
            int m = 0;
            for (int i = 0; i < n; i++)
                if (compat[last * nl + loc[i]]) {
                    loc[m] = loc[i]; lvl[m] = lvl[i]; w[m] = w[i]; m++;
                }
            n = m;
        }
        if (n <= 0)
            break;
        int32_t tot = 0;
        for (int i = 0; i < n; i++) tot += w[i];
        if (tot > 0) {
            int32_t k = jr_int(r, tot);
            for (int i = 0; i < n; i++) {
                k -= w[i];
                if (k < 0) {
                    add_ench(st, ids[loc[i]], lvl[i]);
                    last = loc[i];
                    break;
                }
            }
        }
        cost /= 2;
    }
}

/* the functions of one stack */
static void functions(const int32_t *P, int off, JR *r, Stack *st, int *flags)
{
    if (off <= 0)
        return;
    for (int i = 0; i < P[off]; i++) {
        int fo = P[off + 1 + i];
        if (!conds(P, P[fo + 1], r))
            continue;
        const int32_t *q = P + fo + 2;
        switch (P[fo]) {
        case 1:                                         /* set_count: add, prov */
            st->count = (q[0] ? st->count : 0) + prov_int(P, q[1], r);
            break;
        case 2:                                         /* a float drawn (set_damage) */
            prov_float(P, q[0], r);
            break;
        case 3: {                                       /* enchant_randomly: conv, n, (id, max)* */
            int32_t n = q[1];
            if (n <= 0)
                break;
            int32_t k = jr_int(r, n);
            int32_t lv = mth_int(r, 1, q[3 + 2 * k]);
            add_ench(st, q[2 + 2 * k], lv);
            if (q[0] >= 0)
                st->item = q[0];
            break;
        }
        case 4: {                                       /* enchant_with_levels: conv, prov, data */
            int32_t lv = prov_int(P, q[1], r);
            ench_levels(P, q[2], r, st, lv);
            if (q[0] >= 0)
                st->item = q[0];
            break;
        }
        case 5:                                         /* set_potion */
            st->potion = q[0];
            break;
        case 6: {                                       /* set_stew_effect: n, prov* */
            int32_t n = q[0];
            if (n > 0) {
                int32_t k = jr_int(r, n);
                prov_int(P, q[1 + k], r);
            }
            break;
        }
        case 7:                                         /* set_instrument: nextInt(tag size 4) */
            jr_int(r, 4);
            break;
        case 8:                                         /* an int drawn (ominous amplifier) */
            prov_int(P, q[0], r);
            break;
        case 9: {                                       /* set_enchantments: conv, n, (id, prov)* */
            for (int j = 0; j < q[1]; j++)
                add_ench(st, q[2 + 2 * j], prov_int(P, q[3 + 2 * j], r));
            if (q[0] >= 0)
                st->item = q[0];
            break;
        }
        case 10:                                        /* set_item / exploration_map */
            st->item = q[0];
            break;
        }
    }
}

static inline void jshuffle(int32_t *a, int n, JR *r)
{
    for (int i = n; i > 1; i--) {
        int j = jr_int(r, i);
        int32_t t = a[i - 1]; a[i - 1] = a[j]; a[j] = t;
    }
}

LV_API int lv_version(void) { return 1; }

/* fill one container of `size` slots (27); out[slot] = stack index or -1, the
 * stacks in st[] (≤ MAXS). Returns the number of stacks, or -1; *flags as above */
LV_API int lv_fill(const int32_t *P, int64_t seed, int size, int32_t *slot_of, Stack *outst, int *flags)
{
    JR r;
    jr_seed(&r, seed);
    *flags = 0;
    Stack items[MAXS];
    int ni = 0;
    int t0 = P[0];                                      /* the table header */
    int np = P[t0];
    for (int pi = 0; pi < np; pi++) {
        int po = P[t0 + 1 + pi];
        if (!conds(P, P[po], &r))
            continue;
        int32_t rolls = prov_int(P, P[po + 1], &r);
        int ne = P[po + 2];
        for (int k = 0; k < rolls; k++) {
            int valid[256], nv = 0;
            int32_t tot = 0;
            for (int e = 0; e < ne; e++) {
                int eo = P[po + 3 + e];
                if (!conds(P, P[eo + 2], &r))
                    continue;
                if (P[eo + 1] > 0 && nv < 256) {
                    valid[nv++] = eo;
                    tot += P[eo + 1];
                }
            }
            if (nv == 0 || tot == 0)
                continue;
            int eo = valid[0];
            if (nv > 1) {
                int32_t idx = jr_int(&r, tot);
                for (int v = 0; v < nv; v++) {
                    idx -= P[valid[v] + 1];
                    if (idx < 0) { eo = valid[v]; break; }
                }
            }
            if (P[eo] != 1)
                continue;                               /* empty */
            Stack st;
            memset(&st, 0, sizeof st);
            st.item = P[eo + 3];
            st.count = 1;
            st.potion = -1;
            functions(P, P[eo + 4], &r, &st, flags);
            int32_t mx = P[eo + 5];
            if (st.count < mx) {
                if (ni < MAXS) items[ni++] = st;
            } else {
                int32_t c = st.count;
                while (c > 0 && ni < MAXS) {
                    int32_t kk = c < mx ? c : mx;
                    items[ni] = st;
                    items[ni].count = kk;
                    ni++;
                    c -= kk;
                }
            }
        }
    }
    int32_t slots[64];
    for (int i = 0; i < size; i++) slots[i] = i;
    jshuffle(slots, size, &r);
    /* shuffleAndSplitItems */
    Stack rest[MAXS], split[MAXS];
    int nr = 0, ns = 0;
    for (int i = 0; i < ni; i++) {
        if (items[i].count <= 0)
            continue;
        if (items[i].count > 1) split[ns++] = items[i];
        else rest[nr++] = items[i];
    }
    while (size - nr - ns > 0 && ns > 0 && nr + ns < MAXS - 2) {
        int32_t pick = mth_int(&r, 0, ns - 1);
        Stack st = split[pick];
        for (int j = pick; j < ns - 1; j++) split[j] = split[j + 1];
        ns--;
        int32_t kk = mth_int(&r, 1, st.count / 2);
        Stack cp = st;
        cp.count = kk;
        st.count -= kk;
        if (st.count > 1 && jr_next(&r, 1) != 0) split[ns++] = st;
        else rest[nr++] = st;
        if (cp.count > 1 && jr_next(&r, 1) != 0) split[ns++] = cp;
        else rest[nr++] = cp;
    }
    for (int i = 0; i < ns && nr < MAXS; i++) rest[nr++] = split[i];
    /* java_shuffle of the stacks (indexes) */
    int32_t order[MAXS];
    for (int i = 0; i < nr; i++) order[i] = i;
    jshuffle(order, nr, &r);
    for (int i = 0; i < size; i++) slot_of[i] = -1;
    int free_n = size, out_n = 0;
    for (int i = 0; i < nr; i++) {
        if (free_n == 0) {
            *flags |= 2;                                /* over-filled container */
            break;
        }
        int s = slots[--free_n];
        outst[out_n] = rest[order[i]];
        slot_of[s] = out_n++;
    }
    return out_n;
}

/* many seeds at once (benchmarks / batch): the number of stacks per seed */
LV_API void lv_fill_many(const int32_t *P, const int64_t *seeds, int n, int size,
                         int32_t *slot_of, Stack *outst, int32_t *nst, int32_t *flags)
{
    for (int i = 0; i < n; i++) {
        int f = 0;
        nst[i] = lv_fill(P, seeds[i], size, slot_of + (int64_t)i * size, outst + (int64_t)i * size, &f);
        flags[i] = f;
    }
}

/* ── the loot finder: judge a chest without building it in Python ──────────── */
/* a wanted item: item id (-2: not in any table: never), potion (-1 any), ench (-1 any), level */
static inline int want_match(const Stack *s, const int32_t *w)
{
    if (s->item != w[0])
        return 0;
    if (w[1] >= 0 && s->potion != w[1])
        return 0;
    if (w[2] >= 0) {
        for (int j = 0; j < s->ne; j++)
            if (s->ench[j][0] == w[2] && s->ench[j][1] >= w[3])
                return 1;
        return 0;
    }
    return 1;
}

/* out[w] = how many of want w the chest holds (placed stacks); returns the stacks */
LV_API int lv_count(const int32_t *P, int64_t seed, int size, int nw, const int32_t *W, int32_t *out)
{
    int32_t slot_of[64];
    Stack st[64];
    int f;
    int n = lv_fill(P, seed, size, slot_of, st, &f);
    for (int w = 0; w < nw; w++)
        out[w] = 0;
    for (int i = 0; i < n; i++)
        for (int w = 0; w < nw; w++)
            if (want_match(&st[i], W + 4 * w))
                out[w] += st[i].count;
    return n;
}

/* the Layout cells (kind, n): -1 any, -2 empty, -3 filled, k ≥ 0 want k (n: exact count, 0 any) */
LV_API int lv_layout(const int32_t *P, int64_t seed, int size, const int32_t *W, int ncell,
                     const int32_t *cells)
{
    int32_t slot_of[64];
    Stack st[64];
    int f;
    lv_fill(P, seed, size, slot_of, st, &f);
    int m = ncell < size ? ncell : size;
    for (int k = 0; k < m; k++) {
        int kind = cells[2 * k], n = cells[2 * k + 1];
        int i = slot_of[k];
        if (kind == -1)
            continue;
        if (kind == -2) { if (i >= 0) return 0; continue; }
        if (kind == -3) { if (i < 0) return 0; continue; }
        if (i < 0 || !want_match(&st[i], W + 4 * kind))
            return 0;
        if (n > 0 && st[i].count != n)
            return 0;
    }
    return 1;
}

/* ── XoRoShiRo128++ : n steps (the jungle temple's moss boxes, 1.18+) ──────── */
static inline uint64_t rotl64(uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }

LV_API void lv_xoro_skip(uint64_t *st, int64_t n)
{
    uint64_t lo = st[0], hi = st[1];
    for (int64_t i = 0; i < n; i++) {
        uint64_t h = hi ^ lo;
        lo = rotl64(lo, 49) ^ h ^ (h << 21);
        hi = rotl64(h, 28);
    }
    st[0] = lo;
    st[1] = hi;
}

/* (12/11) loot_exact's layout algebra: the product of two batches of truncated
 * polynomials in v (degree < V1) whose coefficients live in a small algebra of
 * dimension In (the dual x and the cell marks): e_p · e_q = e_rt[p·In + q] (−1: 0).
 * a, b, out: n rows of V1 × In doubles (v, then the inner index); out = a · b
 * truncated at v^(V1 − 1). Only the nonzero coefficients are visited (the marks are
 * mostly zero). */
LV_API void lv_polymul2(const double *a, const double *b, double *out, int n, int V1, int In,
                        const int *rt)
{
    const int S = V1 * In;
    int *ib = (int *)malloc(sizeof(int) * ((size_t)S * 2 + 3 * (size_t)In * In));
    if (!ib) return;
    int *ia = ib + S, *tr = ib + 2 * S, T = 0;
    for (int p = 0; p < In; p++)                     /* the structure constants */
        for (int q = 0; q < In; q++)
            if (rt[p * In + q] >= 0) { tr[3 * T] = p; tr[3 * T + 1] = q; tr[3 * T + 2] = rt[p * In + q]; T++; }
    for (int row = 0; row < n; row++) {
        const double *A = a + (size_t)row * S, *B = b + (size_t)row * S;
        double *O = out + (size_t)row * S;
        int na = 0, nb = 0;
        for (int k = 0; k < S; k++) {
            O[k] = 0.0;
            if (A[k] != 0.0) ia[na++] = k;
            if (B[k] != 0.0) ib[nb++] = k;
        }
        if ((double)na * nb > 0.5 * V1 * V1 * T) {   /* dense: v pairs × constants */
            for (int i = 0; i < V1; i++) {
                const double *Ai = A + i * In;
                for (int j = 0; i + j < V1; j++) {
                    const double *Bj = B + j * In;
                    double *Ok = O + (i + j) * In;
                    for (int t = 0; t < T; t++)
                        Ok[tr[3 * t + 2]] += Ai[tr[3 * t]] * Bj[tr[3 * t + 1]];
                }
            }
            continue;
        }
        for (int x = 0; x < na; x++) {
            int ka = ia[x], i = ka / In, p = ka - i * In;
            double va = A[ka];
            const int *rp = rt + p * In;
            int lim = (V1 - i) * In;                 /* j < V1 − i */
            for (int y = 0; y < nb; y++) {
                int kb = ib[y];
                if (kb >= lim) break;                 /* ib is sorted by v first */
                int j = kb / In, q = kb - j * In, r = rp[q];
                if (r >= 0) O[(i + j) * In + r] += va * B[kb];
            }
        }
    }
    free(ib);
}
