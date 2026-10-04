// topy_gpu_gen.cuh — the 1.13–1.17 stronghold generator (Top Y only), written so
// that the SAME code runs on the GPU (one thread = one stronghold) and on the CPU
// (where it is checked against cubiomes_src/stronghold_generator, 03/10).
//
// It is a line-by-line port of StrongholdGenerator::topY (fullBfs, pre118 filler
// rule) with a compact memory layout:
//   * pieces: 16 bytes each (int16 box + type / orientation / depth / data),
//     at most TG_MAXP; a stronghold with more pieces (≈ 1 in 6 000) returns
//     TG_OVERFLOW and the caller checks it on the CPU instead (exact either way);
//   * collisions: XZ cells of 16 blocks around the start, one ascending linked list
//     of piece indices per cell, so findCollision returns the lowest index that
//     intersects — exactly what the linear scan of Minecraft returns.
#pragma once
#include <stdint.h>

#if defined(__CUDACC__)
#define TG_HD __host__ __device__ inline
#define TG_HDN __host__ __device__ __noinline__
#else
#define TG_HD static inline
#define TG_HDN static
#endif

#define TG_MAXP 512
#define TG_GRID 24
#define TG_NODES 2047                 // node 0x7FF = "none"
#define TG_NIL 0x7FF
#define TG_PW (TG_MAXP / 32)          // words of the pending bitset
#define TG_OVERFLOW (-10000)

enum { TG_STRAIGHT, TG_PRISON_HALL, TG_LEFT_TURN, TG_RIGHT_TURN, TG_ROOM_CROSSING,
       TG_STRAIGHT_STAIRS_DOWN, TG_STAIRS_DOWN, TG_FIVE_CROSSING, TG_CHEST_CORRIDOR,
       TG_LIBRARY, TG_PORTAL_ROOM, TG_FILLER_CORRIDOR, TG_NONE };
enum { TG_NORTH = 0, TG_SOUTH = 1, TG_WEST = 2, TG_EAST = 3 };

typedef struct { int16_t x0, y0, z0, x1, y1, z1; } TgBox;
typedef struct { TgBox b; uint8_t type, dir, depth, data; } TgPiece;

typedef struct {
    uint64_t seed;
} TgRand;

#define TG_MASK ((1ULL << 48) - 1)

TG_HD void tg_set_seed(TgRand *r, uint64_t s) { r->seed = (s ^ 0x5DEECE66DULL) & TG_MASK; }
TG_HD int tg_next(TgRand *r, int bits) {
    r->seed = (r->seed * 0x5DEECE66DULL + 0xBULL) & TG_MASK;
    return (int)(int64_t)(r->seed >> (48 - bits));
}
TG_HD int tg_next_int(TgRand *r, int bound) {
    int v = tg_next(r, 31);
    int m = bound - 1;
    if ((bound & m) == 0)
        return (int)(((int64_t)bound * (int64_t)v) >> 31);
    // Java: for (u = r; u - (r = u % bound) + m < 0; u = next(31)) — the sum
    // overflows on purpose: done in unsigned arithmetic (no signed-overflow UB)
    for (int u = v;; u = tg_next(r, 31)) {
        v = u % bound;
        if ((int)((unsigned)u - (unsigned)v + (unsigned)m) >= 0) break;
    }
    return v;
}
TG_HD int64_t tg_next_long(TgRand *r) {
    int64_t hi = (int64_t)tg_next(r, 32);
    int64_t lo = (int64_t)tg_next(r, 32);
    return (int64_t)((uint64_t)hi << 32) + lo;
}
TG_HD int tg_next_bool(TgRand *r) { return tg_next(r, 1) != 0; }
TG_HD void tg_large_feature_seed(TgRand *r, int64_t world, int cx, int cz) {
    tg_set_seed(r, (uint64_t)world);
    uint64_t a = (uint64_t)tg_next_long(r);
    uint64_t b = (uint64_t)tg_next_long(r);
    uint64_t s = ((uint64_t)(int64_t)cx * a) ^ ((uint64_t)(int64_t)cz * b) ^ (uint64_t)world;
    tg_set_seed(r, s);
}

TG_HD TgBox tg_box(int x0, int y0, int z0, int x1, int y1, int z1) {
    TgBox b; b.x0 = (int16_t)x0; b.y0 = (int16_t)y0; b.z0 = (int16_t)z0;
    b.x1 = (int16_t)x1; b.y1 = (int16_t)y1; b.z1 = (int16_t)z1; return b;
}
TG_HD int tg_intersects(const TgBox *a, const TgBox *b) {
    return a->x1 >= b->x0 && a->x0 <= b->x1 && a->z1 >= b->z0 && a->z0 <= b->z1
        && a->y1 >= b->y0 && a->y0 <= b->y1;
}
TG_HD TgBox tg_orient(int x, int y, int z, int ow, int oh, int od, int w, int h, int d, int dir) {
    switch (dir) {
    case TG_NORTH: return tg_box(x + ow, y + oh, z - d + 1 + od, x + w - 1 + ow, y + h - 1 + oh, z + od);
    case TG_SOUTH: return tg_box(x + ow, y + oh, z + od, x + w - 1 + ow, y + h - 1 + oh, z + d - 1 + od);
    case TG_WEST:  return tg_box(x - d + 1 + od, y + oh, z + ow, x + od, y + h - 1 + oh, z + w - 1 + ow);
    default:       return tg_box(x + od, y + oh, z + ow, x + d - 1 + od, y + h - 1 + oh, z + w - 1 + ow);
    }
}

// weight, max place count, min depth (PieceWeight::PIECE_WEIGHTS)
#define TG_W(t) ((t) == 0 ? 40 : (t) == 1 ? 5 : (t) == 2 ? 20 : (t) == 3 ? 20 : (t) == 4 ? 10 : \
                 (t) == 5 ? 5 : (t) == 6 ? 5 : (t) == 7 ? 5 : (t) == 8 ? 5 : (t) == 9 ? 10 : 20)
#define TG_MAXC(t) ((t) == 0 ? 0 : (t) == 1 ? 5 : (t) == 2 ? 0 : (t) == 3 ? 0 : (t) == 4 ? 6 : \
                    (t) == 5 ? 5 : (t) == 6 ? 5 : (t) == 7 ? 4 : (t) == 8 ? 4 : (t) == 9 ? 2 : 1)
#define TG_MIND(t) ((t) == 9 ? 5 : (t) == 10 ? 6 : 0)

// Pending pieces: Minecraft keeps them in a list, appends new pieces and removes
// the k-th one at random. New pieces always get the next (increasing) index, so
// that list is exactly the set of pending indices in increasing order: a bitset,
// where "the k-th one" is the k-th set bit (no list shifting).
// Grid cells: one 32-bit word = stamp (10 bits) | head (11) | tail (11); a cell
// whose stamp is not the current one is empty, so a new stronghold clears nothing.
// Nodes: piece (high 16 bits) | next node (low 11 bits).
typedef struct {
    TgPiece p[TG_MAXP];
    uint32_t pmask[TG_PW];
    uint32_t gcell[TG_GRID * TG_GRID];
    uint32_t node[TG_NODES];
    uint8_t pcType[11], pcCount[11];
    int n, npend, nnodes, npc, stamp;
    int totalWeight, imposed, previous;
    int startX, startZ, gox, goz, gridOn;
    int portal, stopped, overflow, pre118;
} TgGen;

// once per thread / process: no cell belongs to any stamp yet
TG_HD void tg_init(TgGen *g) {
    for (int c = 0; c < TG_GRID * TG_GRID; c++) g->gcell[c] = 0;
    g->stamp = 0;
}

TG_HD int tg_popc(uint32_t v) {
#if defined(__CUDA_ARCH__)
    return __popc(v);
#else
    int c = 0; while (v) { v &= v - 1; c++; } return c;
#endif
}
TG_HD int tg_ctz(uint32_t v) {          // v != 0
#if defined(__CUDA_ARCH__)
    return __ffs((int)v) - 1;
#else
    int c = 0; while (!(v & 1u)) { v >>= 1; c++; } return c;
#endif
}
TG_HD void tg_pend_add(TgGen *g, int i) { g->pmask[i >> 5] |= 1u << (i & 31); g->npend++; }
TG_HD int tg_pend_take(TgGen *g, int k) {   // remove and return the k-th pending index
    for (int w = 0; w < TG_PW; w++) {
        uint32_t m = g->pmask[w];
        int c = tg_popc(m);
        if (k >= c) { k -= c; continue; }
        while (k--) m &= m - 1;
        int b = tg_ctz(m);
        g->pmask[w] &= ~(1u << b);
        g->npend--;
        return (w << 5) | b;
    }
    return -1;                             // unreachable (k < npend)
}

TG_HDN void tg_grid_insert(TgGen *g, int idx) {
    if (!g->gridOn) return;
    const TgBox *b = &g->p[idx].b;
    int x0 = (b->x0 - g->gox) >> 4, x1 = (b->x1 - g->gox) >> 4;
    int z0 = (b->z0 - g->goz) >> 4, z1 = (b->z1 - g->goz) >> 4;
    if (x0 < 0 || z0 < 0 || x1 >= TG_GRID || z1 >= TG_GRID) { g->gridOn = 0; return; }
    const uint32_t st = (uint32_t)g->stamp << 22;
    for (int gx = x0; gx <= x1; gx++)
        for (int gz = z0; gz <= z1; gz++) {
            if (g->nnodes >= TG_NODES) { g->gridOn = 0; return; }
            int c = gx * TG_GRID + gz, k = g->nnodes++;
            g->node[k] = ((uint32_t)idx << 16) | TG_NIL;
            uint32_t v = g->gcell[c];
            if ((v & 0xFFC00000u) != st) {
                g->gcell[c] = st | ((uint32_t)k << 11) | (uint32_t)k;
            } else {
                int tail = (int)(v & TG_NIL);
                g->node[tail] = (g->node[tail] & 0xFFFF0000u) | (uint32_t)k;
                g->gcell[c] = (v & ~(uint32_t)TG_NIL) | (uint32_t)k;
            }
        }
}

TG_HD int tg_push(TgGen *g, TgBox b, int type, int dir, int depth, int data) {
    if (g->n >= TG_MAXP) { g->overflow = 1; g->stopped = 1; return -1; }
    int i = g->n++;
    g->p[i].b = b; g->p[i].type = (uint8_t)type; g->p[i].dir = (uint8_t)dir;
    g->p[i].depth = (uint8_t)depth; g->p[i].data = (uint8_t)data;
    if (i == 0) {
        g->gox = g->startX - (TG_GRID / 2) * 16;
        g->goz = g->startZ - (TG_GRID / 2) * 16;
        if (++g->stamp >= 1024) {          // stamps wrapped: clear once every 1023 grids
            for (int c = 0; c < TG_GRID * TG_GRID; c++) g->gcell[c] = 0;
            g->stamp = 1;
        }
        g->nnodes = 0; g->gridOn = 1;
    }
    tg_grid_insert(g, i);
    return i;
}

TG_HDN int tg_collide(TgGen *g, const TgBox *q) {   // lowest index intersecting, or -1
    if (g->gridOn) {
        int x0 = (q->x0 - g->gox) >> 4, x1 = (q->x1 - g->gox) >> 4;
        int z0 = (q->z0 - g->goz) >> 4, z1 = (q->z1 - g->goz) >> 4;
        if (x0 >= 0 && z0 >= 0 && x1 < TG_GRID && z1 < TG_GRID) {
            int best = 1 << 30;
            const uint32_t st = (uint32_t)g->stamp << 22;
            for (int gx = x0; gx <= x1; gx++)
                for (int gz = z0; gz <= z1; gz++) {
                    uint32_t v = g->gcell[gx * TG_GRID + gz];
                    if ((v & 0xFFC00000u) != st) continue;
                    for (int k = (int)((v >> 11) & TG_NIL); k != TG_NIL;) {
                        uint32_t nd = g->node[k];
                        int idx = (int)(nd >> 16);
                        if (idx >= best) break;
                        if (tg_intersects(&g->p[idx].b, q)) { best = idx; break; }
                        k = (int)(nd & TG_NIL);
                    }
                }
            return best < (1 << 30) ? best : -1;
        }
    }
    for (int i = 0; i < g->n; i++)
        if (tg_intersects(&g->p[i].b, q)) return i;
    return -1;
}

TG_HD void tg_reset(TgGen *g) {
    for (int i = 0; i < 11; i++) { g->pcType[i] = (uint8_t)i; g->pcCount[i] = 0; }
    g->npc = 11; g->imposed = TG_NONE; g->totalWeight = 145; g->previous = TG_NONE;
    g->n = 0; g->npend = 0; g->portal = -1; g->stopped = 0; g->gridOn = 0;
    for (int w = 0; w < TG_PW; w++) g->pmask[w] = 0;
}

TG_HD TgBox tg_piece_box(int t, int x, int y, int z, int dir) {
    switch (t) {
    case TG_STRAIGHT:             return tg_orient(x, y, z, -1, -1, 0, 5, 5, 7, dir);
    case TG_PRISON_HALL:          return tg_orient(x, y, z, -1, -1, 0, 9, 5, 11, dir);
    case TG_LEFT_TURN:
    case TG_RIGHT_TURN:           return tg_orient(x, y, z, -1, -1, 0, 5, 5, 5, dir);
    case TG_ROOM_CROSSING:        return tg_orient(x, y, z, -4, -1, 0, 11, 7, 11, dir);
    case TG_STRAIGHT_STAIRS_DOWN: return tg_orient(x, y, z, -1, -7, 0, 5, 11, 8, dir);
    case TG_STAIRS_DOWN:          return tg_orient(x, y, z, -1, -7, 0, 5, 11, 5, dir);
    case TG_FIVE_CROSSING:        return tg_orient(x, y, z, -4, -3, 0, 10, 9, 11, dir);
    case TG_CHEST_CORRIDOR:       return tg_orient(x, y, z, -1, -1, 0, 5, 5, 7, dir);
    case TG_LIBRARY:              return tg_orient(x, y, z, -4, -1, 0, 14, 11, 15, dir);
    case TG_PORTAL_ROOM:          return tg_orient(x, y, z, -4, -1, 0, 11, 8, 16, dir);
    }
    return tg_box(0, 0, 0, 0, 0, 0);
}

// findAndCreatePieceFactory: 1 if the piece was placed
TG_HDN int tg_factory(TgGen *g, TgRand *r, int t, int x, int y, int z, int dir, int depth) {
    TgBox b = tg_piece_box(t, x, y, z, dir);
    if (!(b.y0 > 10) || tg_collide(g, &b) >= 0) {
        if (t != TG_LIBRARY) return 0;
        b = tg_orient(x, y, z, -4, -1, 0, 14, 6, 15, dir);
        if (!(b.y0 > 10) || tg_collide(g, &b) >= 0) return 0;
    }
    int data = 0;
    switch (t) {
    case TG_STRAIGHT:
        tg_next_int(r, 5);
        data |= (tg_next_int(r, 2) == 0) << 0;
        data |= (tg_next_int(r, 2) == 0) << 1;
        break;
    case TG_ROOM_CROSSING:
        tg_next_int(r, 5); tg_next_int(r, 5);
        break;
    case TG_FIVE_CROSSING:
        tg_next_int(r, 5);
        data |= tg_next_bool(r) << 0;
        data |= tg_next_bool(r) << 1;
        data |= tg_next_bool(r) << 2;
        data |= (tg_next_int(r, 3) > 0) << 3;
        break;
    case TG_PORTAL_ROOM:
        break;
    default:
        tg_next_int(r, 5);
    }
    if (g->npend >= TG_MAXP || g->n >= TG_MAXP) { g->overflow = 1; g->stopped = 1; return 1; }
    int i = tg_push(g, b, t, dir, depth, data);
    if (t == TG_PORTAL_ROOM && g->portal < 0) g->portal = i;
    tg_pend_add(g, i);
    return 1;
}

TG_HD void tg_update_weight(TgGen *g) {
    for (int i = 0; i < g->npc; i++) {
        int t = g->pcType[i];
        if (TG_MAXC(t) > 0 && g->pcCount[i] < TG_MAXC(t)) return;
    }
    g->stopped = 1;
}

TG_HDN void tg_from_small_door(TgGen *g, TgRand *r, int x, int y, int z, int dir, int depth) {
    if (g->stopped) return;
    if (g->imposed != TG_NONE) {
        int t = g->imposed;
        g->imposed = TG_NONE;
        if (tg_factory(g, r, t, x, y, z, dir, depth)) return;
    }
    for (int attempt = 0; attempt < 5; attempt++) {
        int sel = tg_next_int(r, g->totalWeight);
        for (int k = 0; k < g->npc; k++) {
            int t = g->pcType[k];
            if ((sel -= TG_W(t)) >= 0) continue;
            int maxc = TG_MAXC(t);
            int valid = (maxc == 0 || g->pcCount[k] < maxc);
            if (!(valid && depth >= TG_MIND(t)) || t == g->previous) goto next_attempt;
            if (!tg_factory(g, r, t, x, y, z, dir, depth)) continue;
            g->pcCount[k]++;
            g->previous = t;
            if (!(maxc == 0 || g->pcCount[k] < maxc)) {
                g->totalWeight -= TG_W(t);
                g->npc--;
                for (int j = k; j < g->npc; j++) { g->pcType[j] = g->pcType[j + 1]; g->pcCount[j] = g->pcCount[j + 1]; }
                tg_update_weight(g);
            }
            return;
        }
next_attempt:;
    }
    TgBox b = tg_orient(x, y, z, -1, -1, 0, 5, 5, 4, dir);
    int c = tg_collide(g, &b);
    if (c < 0) return;
    if (g->p[c].b.y0 == b.y0) {
        if (g->pre118) {
            for (int i = 3; i >= 1; --i) {
                b = tg_orient(x, y, z, -1, -1, 0, 5, 5, i - 1, dir);
                if (tg_intersects(&g->p[c].b, &b)) continue;
                b = tg_orient(x, y, z, -1, -1, 0, 5, 5, i, dir);
                if (b.y0 > 1) {
                    if (g->npend >= TG_MAXP || g->n >= TG_MAXP) { g->overflow = 1; g->stopped = 1; return; }
                    int k = tg_push(g, b, TG_FILLER_CORRIDOR, dir, depth, 0);
                    tg_pend_add(g, k);
                }
                return;
            }
        } else {
            for (int i = 2; i >= 1; --i) {
                b = tg_orient(x, y, z, -1, -1, 0, 5, 5, i, dir);
                if (tg_intersects(&g->p[c].b, &b)) continue;
                b = tg_orient(x, y, z, -1, -1, 0, 5, 5, i + 1, dir);
                if (b.y0 > 1) {
                    if (g->npend >= TG_MAXP || g->n >= TG_MAXP) { g->overflow = 1; g->stopped = 1; return; }
                    int k = tg_push(g, b, TG_FILLER_CORRIDOR, dir, depth, 0);
                    tg_pend_add(g, k);
                }
                return;
            }
        }
    }
}

TG_HD void tg_add_piece(TgGen *g, TgRand *r, int x, int y, int z, int dir, int depth) {
    if (depth > 50) return;
    int dx = x - g->startX, dz = z - g->startZ;
    if ((dx < 0 ? -dx : dx) > 112 || (dz < 0 ? -dz : dz) > 112) return;
    tg_from_small_door(g, r, x, y, z, dir, depth + 1);
}

TG_HD void tg_fwd(TgGen *g, TgRand *r, int pi, int n, int n2) {
    TgBox b = g->p[pi].b; int dir = g->p[pi].dir, d = g->p[pi].depth;
    switch (dir) {
    case TG_NORTH: tg_add_piece(g, r, b.x0 + n, b.y0 + n2, b.z0 - 1, dir, d); break;
    case TG_SOUTH: tg_add_piece(g, r, b.x0 + n, b.y0 + n2, b.z1 + 1, dir, d); break;
    case TG_WEST:  tg_add_piece(g, r, b.x0 - 1, b.y0 + n2, b.z0 + n, dir, d); break;
    default:       tg_add_piece(g, r, b.x1 + 1, b.y0 + n2, b.z0 + n, dir, d); break;
    }
}
TG_HD void tg_left(TgGen *g, TgRand *r, int pi, int n, int n2) {
    TgBox b = g->p[pi].b; int dir = g->p[pi].dir, d = g->p[pi].depth;
    if (dir == TG_NORTH || dir == TG_SOUTH) tg_add_piece(g, r, b.x0 - 1, b.y0 + n, b.z0 + n2, TG_WEST, d);
    else                                     tg_add_piece(g, r, b.x0 + n2, b.y0 + n, b.z0 - 1, TG_NORTH, d);
}
TG_HD void tg_right(TgGen *g, TgRand *r, int pi, int n, int n2) {
    TgBox b = g->p[pi].b; int dir = g->p[pi].dir, d = g->p[pi].depth;
    if (dir == TG_NORTH || dir == TG_SOUTH) tg_add_piece(g, r, b.x1 + 1, b.y0 + n, b.z0 + n2, TG_EAST, d);
    else                                     tg_add_piece(g, r, b.x0 + n2, b.y0 + n, b.z1 + 1, TG_SOUTH, d);
}

TG_HDN void tg_children(TgGen *g, TgRand *r, int pi) {
    int t = g->p[pi].type, dir = g->p[pi].dir, data = g->p[pi].data;
    switch (t) {
    case TG_STRAIGHT:
        tg_fwd(g, r, pi, 1, 1);
        if (data & 1) tg_left(g, r, pi, 1, 2);
        if (data & 2) tg_right(g, r, pi, 1, 2);
        break;
    case TG_PRISON_HALL: tg_fwd(g, r, pi, 1, 1); break;
    case TG_LEFT_TURN:
        if (dir == TG_NORTH || dir == TG_EAST) tg_left(g, r, pi, 1, 1); else tg_right(g, r, pi, 1, 1);
        break;
    case TG_RIGHT_TURN:
        if (dir == TG_NORTH || dir == TG_EAST) tg_right(g, r, pi, 1, 1); else tg_left(g, r, pi, 1, 1);
        break;
    case TG_ROOM_CROSSING:
        tg_fwd(g, r, pi, 4, 1); tg_left(g, r, pi, 1, 4); tg_right(g, r, pi, 1, 4);
        break;
    case TG_STRAIGHT_STAIRS_DOWN: tg_fwd(g, r, pi, 1, 1); break;
    case TG_STAIRS_DOWN:
        if (data != 0) g->imposed = TG_FIVE_CROSSING;
        tg_fwd(g, r, pi, 1, 1);
        break;
    case TG_FIVE_CROSSING: {
        int n = 3, n2 = 5;
        if (dir == TG_WEST || dir == TG_NORTH) { n = 8 - n; n2 = 8 - n2; }
        tg_fwd(g, r, pi, 5, 1);
        if (data & 1) tg_left(g, r, pi, n, 1);
        if (data & 2) tg_left(g, r, pi, n2, 7);
        if (data & 4) tg_right(g, r, pi, n, 1);
        if (data & 8) tg_right(g, r, pi, n2, 7);
    } break;
    case TG_CHEST_CORRIDOR: tg_fwd(g, r, pi, 1, 1); break;
    default: break;
    }
}

// Top Y after moveBelowSeaLevel (StrongholdGenerator::topY, fullBfs).
// Returns TG_OVERFLOW when the stronghold needs more than TG_MAXP pieces.
TG_HDN int tg_topy(TgGen *g, int64_t worldSeed, int cx, int cz, int sea, int margin,
                   int minY, int inflate, int pre118) {
    const int HORIZ[4] = { TG_NORTH, TG_EAST, TG_SOUTH, TG_WEST };
    int attempt = 0;
    TgRand r;
    const int room = (inflate & 256) != 0;      // top of the portal room (03/10)
    inflate &= 255;
    g->pre118 = pre118;
    g->overflow = 0;
    do {
        tg_reset(g);
        tg_large_feature_seed(&r, (int64_t)((uint64_t)worldSeed + (uint64_t)(int64_t)attempt++), cx, cz);
        g->startX = (cx << 4) + 2;
        g->startZ = (cz << 4) + 2;
        int dir = HORIZ[tg_next_int(&r, 4)];
        int x = g->startX, z = g->startZ;
        TgBox b = (dir == TG_NORTH || dir == TG_SOUTH)
            ? tg_box(x, 64, z, x + 4, 74, z + 4) : tg_box(x, 64, z, x + 4, 74, z + 4);
        tg_push(g, b, TG_STAIRS_DOWN, dir, 0, 1);
        tg_children(g, &r, 0);
        while (g->npend != 0) {
            if (g->overflow) return TG_OVERFLOW;
            int pi = tg_pend_take(g, tg_next_int(&r, g->npend));
            tg_children(g, &r, pi);
        }
        if (g->overflow) return TG_OVERFLOW;
        if (attempt > 64) return TG_OVERFLOW;     // never seen: let the CPU decide
    } while (g->portal < 0);
    int gMin = g->p[0].b.y0, gMax = g->p[0].b.y1;
    for (int i = 1; i < g->n; i++) {
        if (g->p[i].b.y0 < gMin) gMin = g->p[i].b.y0;
        if (g->p[i].b.y1 > gMax) gMax = g->p[i].b.y1;
    }
    int realMax = gMax;
    gMin -= inflate; gMax += inflate;
    int yspan = gMax - gMin + 1;
    int n3 = sea - margin;
    int n5 = yspan + minY + 1;
    if (n5 < n3) n5 += tg_next_int(&r, n3 - n5);
    if (room) return g->p[g->portal].b.y1 + (n5 - gMax);
    return realMax + (n5 - gMax);
}
