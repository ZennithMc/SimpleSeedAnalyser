/*
 * treecrack_core.h — (05/11) SSA's tree cracker: the trees of one population chunk
 * (positions, types, heights, corner leaves) → the decoration seed → the structure
 * seed. Shared by the CPU library (treecrack.cpp, g++) and the CUDA unit
 * (tree_cuda.cu, nvcc): every function here is plain C usable on both sides.
 *
 * Written for SSA from the game's logic. The method (a lattice over the first tree's
 * position, then position / attribute windows, then an exact replay of the chunk's
 * tree feature, then population chunk reversal) is the one of Andrew (Gaider10)'s
 * TreeCracker and PopulationCrr, also used by NelS' tree-cracker fork — see
 * THIRD_PARTY_LICENSES.txt. The tree models were checked against real 1.16.1,
 * 1.16.5 and 1.17.1 server worlds (tools/servercheck/treecheck.py).
 *
 * Versions:  TCV_1_14    (1.14 – 1.14.4)   forests (Gaider10 / NelS test data)
 *            TCV_1_15    (1.15 – 1.15.2)   forests (test data)
 *            TCV_1_16_1  (1.16 – 1.16.1)   checked on a server
 *            TCV_1_16_5  (1.16.2 – 1.16.5) checked on a server
 *            TCV_1_17    (1.17 – 1.17.1)   checked on a server
 * Biomes:    TCB_FOREST (oak 72 %, birch 20 %, fancy oak 8 %), TCB_BIRCH_FOREST (birch),
 *            TCB_TAIGA (spruce 2/3, pine 1/3 — 1.16+, (06/11) checked on servers)
 */
#ifndef TREECRACK_CORE_H
#define TREECRACK_CORE_H

#include <stdint.h>
#include <math.h>

#ifdef __CUDACC__
#define TC_FN __host__ __device__ static inline
#else
#define TC_FN static inline
#endif

#define TC_MULT 0x5DEECE66DULL
#define TC_ADD 0xBULL
#define TC_MASK 0xFFFFFFFFFFFFULL
#define TC_MULT_INV 0xDFE05BCB1365ULL      /* TC_MULT^-1 mod 2^48 */
#define TC_M2 0xBB20B4600A69ULL      /* two steps: s → M2 s + A2 */
#define TC_A2 0x40942DE6BAULL
#define TC_M4 0x32EB772C5F11ULL      /* four steps */
#define TC_A4 0x2D3873C4CD04ULL

enum { TCV_1_16_1 = 1, TCV_1_16_5 = 2, TCV_1_17 = 3, TCV_1_14 = 4, TCV_1_15 = 5 };
enum { TCB_FOREST = 0, TCB_BIRCH_FOREST = 1, TCB_TAIGA = 2 };
enum { TCT_OAK = 1, TCT_BIRCH = 2, TCT_FANCY = 4, TCT_SPRUCE = 8, TCT_PINE = 16 };   /* type bits */

#define TC_MAX_TREES 16
#define TC_MAX_COUNT 11                 /* 10 trees + 1 (10 % of chunks) */
#define TC_TREE_CALLS 26                /* most random calls one forest tree can use */
#define TC_WINDOW (TC_MAX_COUNT * TC_TREE_CALLS)

/* One observed tree. x, z: offset in the population chunk (0..15).
   types: the types it may be (TCT_* bits; 0 = unknown).
   hmin / hmax per type index (0 oak, 1 birch, 2 fancy, 3 spruce, 4 pine): the number
   of logs of the trunk (fancy oak: the generator's height 3..14, the UI converts the
   visible trunk; not used before 1.16); -1 = no bound.
   Leaves (oak / birch): 12 corners, bit = layer * 4 + corner, layer 0 = the lowest
   of the 3 random layers (two below the top log), 1 = one below the top log,
   2 = the top log's layer; corners NW, SW, NE, SE (north = -z, west = -x).
   leaf_known: the corners seen; leaf_val: those with a leaf.
   Spruce (amin / amax): [0] logs below the lowest leaves, [1] leaf layers above the
   top log, [2] widest radius, [3] radius of the top layer. Pine (pmin / pmax): [0]
   leaf layers, [1] widest radius. */
typedef struct {
    int32_t x, z;
    int32_t types;
    int32_t hmin[5], hmax[5];
    int32_t leaf_known, leaf_val;
    int32_t amin[4], amax[4];
    int32_t pmin[2], pmax[2];
} TcTree;

typedef struct {
    int32_t version, biome;
    int32_t cx, cz;                     /* population chunk */
    int32_t salt;                       /* decoration salt (80001 1.16, 80002 1.17) */
    int32_t ntrees;
    TcTree t[TC_MAX_TREES];
} TcChunk;

/* ── java.util.Random on the raw 48-bit state ─────────────────────────────── */
TC_FN uint64_t tc_step(uint64_t s) { return (s * TC_MULT + TC_ADD) & TC_MASK; }
TC_FN uint64_t tc_back(uint64_t s) { return ((s - TC_ADD) * TC_MULT_INV) & TC_MASK; }

TC_FN int32_t tc_next(uint64_t *s, int bits) {
    *s = tc_step(*s);
    return (int32_t)(*s >> (48 - bits));
}

TC_FN int32_t tc_int(uint64_t *s, int32_t bound) {
    if ((bound & -bound) == bound)
        return (int32_t)(((uint64_t)bound * (uint64_t)tc_next(s, 31)) >> 31);
    int32_t bits, val;
    do {
        bits = tc_next(s, 31);
        val = bits % bound;
    } while ((int64_t)bits - val + (bound - 1) >= 2147483648LL);
    return val;
}

TC_FN float tc_float(uint64_t *s) { return (float)tc_next(s, 24) / (float)(1 << 24); }

/* the state n calls later (n may be negative): (a, c) of s → a*s + c by squaring */
TC_FN uint64_t tc_skip(uint64_t s, int64_t n) {
    uint64_t m = TC_MULT, a = TC_ADD;
    if (n < 0) { m = TC_MULT_INV; a = (0 - TC_ADD * TC_MULT_INV) & TC_MASK; n = -n; }
    uint64_t am = 1, ac = 0;
    while (n) {
        if (n & 1) { am = (am * m) & TC_MASK; ac = (ac * m + a) & TC_MASK; }
        a = (a * m + a) & TC_MASK;
        m = (m * m) & TC_MASK;
        n >>= 1;
    }
    return (s * am + ac) & TC_MASK;
}

/* ── the forest tree feature ──────────────────────────────────────────────── */

/* the type the selector picks (the state is just after the z call) */
TC_FN int tc_pick_type(uint64_t *s, int biome) {
    if (biome == TCB_BIRCH_FOREST) return TCT_BIRCH;
    if (biome == TCB_TAIGA) return tc_float(s) < 0.33333334f ? TCT_PINE : TCT_SPRUCE;
    if (tc_float(s) < 0.2f) return TCT_BIRCH;
    if (tc_float(s) < 0.1f) return TCT_FANCY;
    return TCT_OAK;
}

TC_FN int tc_type_index(int type) {
    return type == TCT_OAK ? 0 : type == TCT_BIRCH ? 1 : type == TCT_FANCY ? 2 : type == TCT_SPRUCE ? 3 : 4;
}

/* 1.14: heights in one call, leaves bottom-up, no beehive */
TC_FN int tc_v_old(int v) { return v == TCV_1_14; }
/* 1.14 / 1.15: the fancy oak draws from its own Random (one nextLong) */
TC_FN int tc_v_bigtree(int v) { return v == TCV_1_14 || v == TCV_1_15; }

TC_FN int tc_in_range(const TcTree *t, int ti, int32_t v) {
    if (t->hmin[ti] >= 0 && v < t->hmin[ti]) return 0;
    if (t->hmax[ti] >= 0 && v > t->hmax[ti]) return 0;
    return 1;
}

TC_FN int tc_in(int32_t lo, int32_t hi, int32_t v) {
    return !((lo >= 0 && v < lo) || (hi >= 0 && v > hi));
}

/* spruce (1.16+): trunk, logs under the leaves, layers above the top log, widest
   radius, top radius — from the state after the type. Returns the state after the
   draws a grown tree makes. */
TC_FN uint64_t tc_spruce(uint64_t s, int *trunk, int *bare, int *above, int *widest, int *top) {
    int t = 5 + tc_int(&s, 3); t += tc_int(&s, 2);
    int ft = 1 + tc_int(&s, 2);
    int fh = t - ft; if (fh < 4) fh = 4;
    int rad = 2 + tc_int(&s, 2);
    int off = tc_int(&s, 3);
    int tp = tc_int(&s, 2);
    *trunk = t; *bare = t - fh; *above = off + 1; *top = tp;
    *widest = (fh + off + 1 + tp >= 8) ? rad : 2;
    return s;
}

/* pine (1.16+): trunk, leaf layers, widest radius */
TC_FN uint64_t tc_pine(uint64_t s, int version, int grown, int *trunk, int *layers, int *widest) {
    int t = 6 + tc_int(&s, 5); tc_int(&s, 1);
    int fh = 3 + tc_int(&s, 2);
    if (version == TCV_1_16_1) tc_int(&s, 1);              /* radius: 1 + nextInt(1) + … */
    int b = t - fh + 1; if (b < 1) b = 1;
    int fr = 1 + tc_int(&s, b);
    if (version == TCV_1_16_1 && grown) tc_int(&s, 1);     /* foliage offset */
    *trunk = t; *layers = fh + 1 - (fr == 1); *widest = fr < fh - 1 ? fr : fh - 1;
    return s;
}

/* number of random branch nodes of a fancy oak of height h (1.16.2+) */
TC_FN int tc_fancy_nodes(int h) {
    return (int)((0x765543321000ULL >> ((h - 3) * 4)) & 0xF);
}

/* 1.16.1 fancy oak: the foliage blobs its branches make (each takes one call) */
TC_FN int tc_fancy_blobs_1161(uint64_t *s, int h) {
    int nodes = tc_fancy_nodes(h), blobs = 1;
    int n4 = h + 2;
    for (int i = 0; i < nodes; i++) {
        int n2 = n4 - 5 - i;
        float f;
        if ((float)n2 < (float)n4 * 0.3f) f = -1.0f;
        else {
            float a = (float)n4 / 2.0f, b = a - (float)n2, c;
            if (b == 0.0f) c = a;
            else if ((b < 0 ? -b : b) >= a) c = 0.0f;
            else c = sqrtf(a * a - b * b);
            f = c * 0.5f;
        }
        double d4 = 1.0 * (double)f * ((double)tc_float(s) + 0.328);
        double d2 = (double)(tc_float(s) * 2.0f) * 3.141592653589793;
        int ex = (int)floor(d4 * sin(d2) + 0.5), ez = (int)floor(d4 * cos(d2) + 0.5);
        int ey = n2 - 1;
        int sy = (int)(ey - sqrt((double)(ex * ex + ez * ez)) * 0.381);
        if (sy >= n4 * 0.2) blobs++;
    }
    return blobs;
}

/* The type, height and leaves of a tree, from the state just after its z call.
   The tree is assumed to have grown (it was seen). */
TC_FN int tc_test_data(uint64_t s, const TcTree *t, int version, int biome) {
    int type = tc_pick_type(&s, biome);
    if (t->types && !(t->types & type)) return 0;
    int ti = tc_type_index(type);
    if (type == TCT_SPRUCE) {
        int tr, bare, above, widest, top;
        tc_spruce(s, &tr, &bare, &above, &widest, &top);
        return tc_in_range(t, ti, tr) && tc_in(t->amin[0], t->amax[0], bare) &&
               tc_in(t->amin[1], t->amax[1], above) && tc_in(t->amin[2], t->amax[2], widest) &&
               tc_in(t->amin[3], t->amax[3], top);
    }
    if (type == TCT_PINE) {
        int tr, layers, widest;
        tc_pine(s, version, 1, &tr, &layers, &widest);
        return tc_in_range(t, ti, tr) && tc_in(t->pmin[0], t->pmax[0], layers) &&
               tc_in(t->pmin[1], t->pmax[1], widest);
    }
    if (type == TCT_FANCY) {
        if (tc_v_bigtree(version)) return 1;            /* its height: another Random */
        int h = 3 + tc_int(&s, 12);
        return tc_in_range(t, ti, h);
    }
    int h = (type == TCT_OAK ? 4 : 5) + tc_int(&s, 3);
    if (!tc_in_range(t, ti, h)) return 0;
    if (!t->leaf_known) return 1;
    if (tc_v_old(version)) {                             /* 1.14: bottom → top */
        for (int L = 0; L <= 2; L++) {
            for (int c = 0; c < 4; c++) {
                int bit = L * 4 + c;
                s = tc_step(s);
                if ((t->leaf_known >> bit & 1) && (int)(t->leaf_val >> bit & 1) != (int)(s >> 47)) return 0;
            }
        }
        return 1;
    }
    /* nextInt(1) of the height, [1.16.1: foliage radius + offset; 1.15: foliage
       height + radius], the top layer's 4 corners (never kept): 5 or 7 calls */
    if (version == TCV_1_16_1 || version == TCV_1_15) s = tc_step(tc_step(s));
    s = (s * TC_M4 + TC_A4) & TC_MASK;
    s = tc_step(s);
    for (int L = 2; L >= 0; L--) {         /* the layers top → bottom */
        for (int c = 0; c < 4; c++) {
            int bit = L * 4 + c;
            s = tc_step(s);
            if ((t->leaf_known >> bit & 1) && (int)(t->leaf_val >> bit & 1) != (int)(s >> 47)) return 0;
        }
    }
    return 1;
}

/* Does the tree that starts at state `s` (just before its x call) match `t`? */
TC_FN int tc_test_tree(uint64_t s, const TcTree *t, int version, int biome) {
    if (tc_int(&s, 16) != t->x) return 0;
    if (tc_int(&s, 16) != t->z) return 0;
    return tc_test_data(s, t, version, biome);
}

/* the position (and type) only: a quick pre-check */
TC_FN int tc_test_pos(uint64_t s, const TcTree *t, int biome) {
    if (tc_int(&s, 16) != t->x) return 0;
    if (tc_int(&s, 16) != t->z) return 0;
    if (!t->types) return 1;
    return (t->types & tc_pick_type(&s, biome)) != 0;
}

/* Advance over one tree of the feature (from just before its x call), grown or not.
   Returns the state after it and sets *x, *z, *type. */
TC_FN uint64_t tc_run_tree(uint64_t s, int version, int biome, int grown, int *x, int *z, int *type) {
    *x = tc_int(&s, 16);
    *z = tc_int(&s, 16);
    *type = tc_pick_type(&s, biome);
    if (*type == TCT_SPRUCE) {
        tc_int(&s, 3); tc_int(&s, 2); tc_int(&s, 2); tc_int(&s, 2);
        if (grown) { tc_int(&s, 3); tc_int(&s, 2); }   /* foliage offset, top radius */
        return s;
    }
    if (*type == TCT_PINE) {
        int a, b, c;
        return tc_pine(s, version, grown, &a, &b, &c);
    }
    if (*type == TCT_FANCY) {
        if (tc_v_bigtree(version)) {
            s = tc_skip(s, 2);                             /* nextLong: the tree's own Random */
            if (version == TCV_1_15 && grown) s = tc_step(s);   /* beehive */
            return s;
        }
        int h = 3 + tc_int(&s, 12);
        tc_int(&s, 1);
        if (version == TCV_1_16_1) {
            tc_int(&s, 1);                                 /* foliage radius */
            if (grown) {
                int blobs = tc_fancy_blobs_1161(&s, h);
                s = tc_skip(s, blobs + 1);                 /* blob offsets + beehive */
            }
        } else if (grown) {
            s = tc_skip(s, 2 * tc_fancy_nodes(h) + 1);     /* branches + beehive */
        }
        return s;
    }
    tc_int(&s, 3);
    if (tc_v_old(version)) {
        if (grown) s = tc_skip(s, 16);                     /* leaves */
        return s;
    }
    tc_int(&s, 1);
    if (version == TCV_1_15) {
        s = tc_skip(s, 2);                                 /* foliage height, radius */
        if (grown) s = tc_skip(s, 16 + 1 + 1);             /* leaves, trunk top, beehive */
    } else if (version == TCV_1_16_1) {
        tc_int(&s, 1);                                     /* foliage radius */
        if (grown) s = tc_skip(s, 1 + 16 + 1);             /* offset, leaves, beehive */
    } else if (grown) {
        s = tc_skip(s, 16 + 1);                            /* leaves, beehive */
    }
    return s;
}

/* ── exact replay ─────────────────────────────────────────────────────────── */

/* Replays the tree feature from its start state `d` (the decoration random, just
   set): each tree may have grown or not (blocked); returns 1 when some choice makes
   every observed tree appear among the grown ones. Depth-first over the choices. */
TC_FN int tc_replay(uint64_t d, const TcChunk *c) {
    uint64_t s = d;
    int count = 10 + (tc_float(&s) < 0.1f ? 1 : 0);
    uint32_t all = (c->ntrees >= 32) ? 0xFFFFFFFFu : ((1u << c->ntrees) - 1u);
    /* stack of (tree index, state, found, next choice) */
    uint64_t st_s[TC_MAX_COUNT + 1];
    uint32_t st_f[TC_MAX_COUNT + 1];
    int st_c[TC_MAX_COUNT + 1];
    int depth = 0;
    st_s[0] = s; st_f[0] = 0; st_c[0] = 0;
    while (depth >= 0) {
        if (st_f[depth] == all) return 1;
        if (depth == count || st_c[depth] == 2) { depth--; continue; }
        int grown = st_c[depth] == 0 ? 1 : 0;      /* try "grown" first */
        st_c[depth]++;
        /* trees left can't cover the observed ones still missing */
        int missing = 0;
        for (int j = 0; j < c->ntrees; j++) missing += !(st_f[depth] >> j & 1);
        if (missing > count - depth) { st_c[depth] = 2; continue; }
        uint32_t f = st_f[depth];
        if (grown) {
            for (int j = 0; j < c->ntrees; j++)
                if (!(f >> j & 1) && tc_test_tree(st_s[depth], &c->t[j], c->version, c->biome))
                    f |= 1u << j;
        }
        int x, z, ty;
        uint64_t n = tc_run_tree(st_s[depth], c->version, c->biome, grown, &x, &z, &ty);
        depth++;
        st_s[depth] = n; st_f[depth] = f; st_c[depth] = 0;
    }
    return 0;
}



/* How well the start state `d` explains the chunk: the fewest trees of the replay
   that are neither one of the observed trees nor hidden (a tree that did not grow
   because its spot was under a canopy — `cover`, 256 bits, x + 16 z — or grew
   where nothing was entered). 0 = every tree of the feature is accounted for.
   Ranks the seeds of one chunk: false ones need trees that should be visible. */
TC_FN int tc_replay_cost(uint64_t d, const TcChunk *c, const uint32_t *cover) {
    uint64_t s = d;
    int count = 10 + (tc_float(&s) < 0.1f ? 1 : 0);
    uint32_t all = (1u << c->ntrees) - 1u;
    uint64_t st_s[TC_MAX_COUNT + 1];
    uint32_t st_f[TC_MAX_COUNT + 1];
    int st_c[TC_MAX_COUNT + 1], st_k[TC_MAX_COUNT + 1];
    int best = 99, depth = 0;
    st_s[0] = s; st_f[0] = 0; st_c[0] = 0; st_k[0] = 0;
    while (depth >= 0) {
        if (st_k[depth] >= best || st_c[depth] == 2) { depth--; continue; }
        if (depth == count) {
            if (st_f[depth] == all && st_k[depth] < best) best = st_k[depth];
            depth--;
            continue;
        }
        int grown = st_c[depth] == 0 ? 1 : 0;
        st_c[depth]++;
        uint32_t f = st_f[depth];
        int k = st_k[depth], hit = 0;
        if (grown) {
            for (int j = 0; j < c->ntrees; j++)
                if (!(f >> j & 1) && tc_test_tree(st_s[depth], &c->t[j], c->version, c->biome)) {
                    f |= 1u << j; hit = 1; break;
                }
        }
        int x, z, ty;
        uint64_t n = tc_run_tree(st_s[depth], c->version, c->biome, grown, &x, &z, &ty);
        if (grown && !hit) k++;                                   /* a tree nobody saw */
        if (!grown && !(cover[(x + 16 * z) >> 5] >> ((x + 16 * z) & 31) & 1)) k++;  /* in the open */
        depth++;
        st_s[depth] = n; st_f[depth] = f; st_c[depth] = 0; st_k[depth] = k;
    }
    return best;
}

/* the decoration start state ↔ the population seed (48 bits) */
TC_FN uint64_t tc_deco_state(uint64_t popseed, int32_t salt) {
    return ((popseed + (uint64_t)(int64_t)salt) ^ TC_MULT) & TC_MASK;
}
TC_FN uint64_t tc_popseed_of(uint64_t deco_state, int32_t salt) {
    return ((deco_state ^ TC_MULT) - (uint64_t)(int64_t)salt) & TC_MASK;
}

/* the population seed of block (bx, bz) for a structure seed (1.13+), 48 bits */
TC_FN uint64_t tc_popseed(uint64_t ss, int64_t bx, int64_t bz) {
    uint64_t s = (ss ^ TC_MULT) & TC_MASK;
    uint64_t hi = (uint64_t)(int64_t)tc_next(&s, 32), lo = (uint64_t)(int64_t)tc_next(&s, 32);
    uint64_t a = ((hi << 32) + lo) | 1;
    hi = (uint64_t)(int64_t)tc_next(&s, 32); lo = (uint64_t)(int64_t)tc_next(&s, 32);
    uint64_t b = ((hi << 32) + lo) | 1;
    return ((uint64_t)bx * a + (uint64_t)bz * b ^ ss) & TC_MASK;
}

/* What the tree that starts at `s` looks like when it grows: position, type,
   height (logs for oak / birch / spruce / pine, the generator's height for a fancy
   oak, -1 before 1.16) and *leaves: oak / birch the 12 corner leaves (TcTree bits);
   spruce bare | above << 4 | widest << 8 | top << 12; pine layers | widest << 8.
   Returns the state after it (grown or not as asked). */
TC_FN uint64_t tc_tree_info(uint64_t s, int version, int biome, int grown,
                            int *x, int *z, int *type, int *h, int *leaves) {
    uint64_t t = s;
    *x = tc_int(&t, 16);
    *z = tc_int(&t, 16);
    *type = tc_pick_type(&t, biome);
    *leaves = 0;
    if (*type == TCT_SPRUCE) {
        int bare, above, widest, top;
        tc_spruce(t, h, &bare, &above, &widest, &top);
        *leaves = bare | above << 4 | widest << 8 | top << 12;
    } else if (*type == TCT_PINE) {
        int layers, widest;
        tc_pine(t, version, 1, h, &layers, &widest);
        *leaves = layers | widest << 8;
    } else if (*type == TCT_FANCY) {
        *h = tc_v_bigtree(version) ? -1 : 3 + tc_int(&t, 12);
    } else {
        *h = (*type == TCT_OAK ? 4 : 5) + tc_int(&t, 3);
        if (tc_v_old(version)) {
            for (int L = 0; L <= 2; L++)
                for (int c = 0; c < 4; c++)
                    *leaves |= tc_int(&t, 2) << (L * 4 + c);
        } else {
            tc_int(&t, 1);
            if (version == TCV_1_16_1 || version == TCV_1_15) { tc_int(&t, 1); tc_int(&t, 1); }
            t = tc_skip(t, 4);
            for (int L = 2; L >= 0; L--)
                for (int c = 0; c < 4; c++)
                    *leaves |= tc_int(&t, 2) << (L * 4 + c);
        }
    }
    int a, b, ty;
    return tc_run_tree(s, version, biome, grown, &a, &b, &ty);
}

/* ── population chunk reversal (1.13+) ────────────────────────────────────────
   P = (X a + Z b) ^ S mod 2^48, a = nextLong()|1, b = nextLong()|1 of setSeed(S).
   Bits of S are chosen from the lowest up. With n bits known, a and b are known
   modulo 2^min(n-16, 32) (their low 32 bits are the low bits of the 2nd / 4th
   states >> 16, which only depend on the low bits of S), so X a + Z b is known
   modulo 2^(that + t), t = trailing zeros of X | Z: those bits of P are checked
   at every step. The few full candidates are verified with tc_popseed. */

TC_FN int tc_ctz64(uint64_t v) { int n = 0; if (!v) return 64; while (!(v & 1)) { v >>= 1; n++; } return n; }

TC_FN int tc_reverse_pop(uint64_t P, int64_t bx, int64_t bz, uint64_t *out, int cap) {
    P &= TC_MASK;
    if (bx == 0 && bz == 0) { if (cap > 0) out[0] = P; return 1; }
    int t = tc_ctz64((uint64_t)bx | (uint64_t)bz);
    uint64_t X = (uint64_t)bx, Z = (uint64_t)bz;
    /* explicit stack: (S, known bits) */
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
        if (n == 48) {
            if (tc_popseed(S, bx, bz) == P) { if (found < cap) out[found] = S; found++; }
            continue;
        }
        st_s[sp] = S | (1ULL << n); st_n[sp] = n + 1; sp++;
        st_s[sp] = S; st_n[sp] = n + 1; sp++;
    }
    return found;
}

/* ── the lattice of the first tree ─────────────────────────────────────────────
   States s with x = s >> 44 and z = step(s) >> 44: the points (s, M s mod 2^48) of
   a lattice of determinant 2^48 in a 2^44 × 2^44 box → 2^40 of them. Reduced basis
   (Gauss) b1 = (7847617, 4824621), b2 = (-18218081, 24667315); the box is walked in
   sheared coordinates (i', j'), i = i' + ⌊k j'⌋, j = j' + ⌊l i⌋, a bijection, so each
   point is met once; a margin of 2 covers the rounding at the edges. */
#define TC_B00 7847617LL
#define TC_B01 -18218081LL
#define TC_B10 4824621LL
#define TC_B11 24667315LL
#define TC_KL_BITS 32

typedef struct {
    int64_t k_int, l_int;          /* k, l in 32.32 fixed point */
    int64_t i0, j0;                /* first grid point */
    int64_t ci, cj;                /* grid size */
} TcLattice;

TC_FN void tc_lattice(TcLattice *L, int x, int z) {
    double det = (double)(TC_B00 * TC_B11 - TC_B01 * TC_B10);
    double bi00 = (double)TC_B11 / det, bi01 = -(double)TC_B01 / det;
    double bi10 = -(double)TC_B10 / det, bi11 = (double)TC_B00 / det;
    double Si = bi00, l = bi10 / Si;
    double Sj = bi11 - bi01 * l, k = bi01 / Sj;
    L->k_int = (int64_t)floor(k * 4294967296.0 + 0.5);
    L->l_int = (int64_t)floor(l * 4294967296.0 + 0.5);
    double W = 17592186044416.0;                    /* 2^44 */
    int64_t Ri = (int64_t)ceil(Si * W), Rj = (int64_t)ceil(Sj * W);
    L->ci = Ri + 1 + 4;
    L->cj = Rj + 1 + 4;
    L->i0 = (int64_t)floor((double)x * W * Si + 0.5) - 2;
    L->j0 = (int64_t)floor((double)z * W * Sj + 0.5) - 2;
}

/* the state of grid point (gi, gj) (0 ≤ gi < ci, 0 ≤ gj < cj) */
TC_FN uint64_t tc_lattice_state(const TcLattice *L, int64_t gi, int64_t gj) {
    int64_t i = L->i0 + gi, j = L->j0 + gj;
    i += (j * L->k_int) >> TC_KL_BITS;
    j += (i * L->l_int) >> TC_KL_BITS;
    return (uint64_t)(TC_B00 * i + TC_B01 * j) & TC_MASK;
}

/* Filter 1: the grid point is the state just after tree 0's x call. Returns 1 and
   the state before that call in *out when tree 0 matches. */
TC_FN int tc_filter1(uint64_t s1, const TcChunk *c, uint64_t *out) {
    if ((int)(s1 >> 44) != c->t[0].x) return 0;
    uint64_t s2 = tc_step(s1);
    if ((int)(s2 >> 44) != c->t[0].z) return 0;
    if (!tc_test_data(s2, &c->t[0], c->version, c->biome)) return 0;
    *out = tc_back(s1);
    return 1;
}

/* Filters 2 + 3: every other tree starts within TC_WINDOW calls of tree 0. */
TC_FN int tc_filter_window(uint64_t s0, const TcChunk *c) {
    if (c->ntrees <= 1) return 1;
    uint32_t need = ((1u << c->ntrees) - 1u) & ~1u, pos = 0, xmask = 0;
    for (int j = 1; j < c->ntrees; j++) xmask |= 1u << c->t[j].x;
    uint64_t s = tc_skip(s0, -TC_WINDOW);
    for (int d = -TC_WINDOW; d <= TC_WINDOW; d++) {
        int x = (int)(tc_step(s) >> 44);
        if (d != 0 && (xmask >> x & 1)) {
            for (int j = 1; j < c->ntrees; j++)
                if (c->t[j].x == x && !(pos >> j & 1) && tc_test_pos(s, &c->t[j], c->biome)
                    && tc_test_tree(s, &c->t[j], c->version, c->biome))
                    pos |= 1u << j;
        }
        s = tc_step(s);
    }
    return (pos & need) == need;
}

/* Filter 4: the decoration starts c calls before tree 0 (0 ≤ c ≤ TC_WINDOW). Writes
   the matching start states (raw random state) to out; returns how many. */
TC_FN int tc_filter_exact(uint64_t s0, const TcChunk *c, uint64_t *out, int cap) {
    int n = 0;
    uint64_t d = s0;
    for (int back = 0; back <= TC_WINDOW; back++) {
        if (tc_replay(d, c)) {
            if (n < cap) out[n] = d;
            n++;
        }
        d = tc_back(d);
    }
    return n;
}

#endif
