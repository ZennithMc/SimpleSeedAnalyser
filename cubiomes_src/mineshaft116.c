/* mineshaft116.c — Minecraft Java 1.16.1 abandoned mineshafts (see mineshaft116.h). */
#include "mineshaft116.h"
#include "rng.h"
#include <stdlib.h>
#include <string.h>

#define SEA_LEVEL 63

/* ───────────────────────── boxes ───────────────────────── */

static inline int box_isect(const Ms116Box *a, const Ms116Box *b)
{
    return a->x1 >= b->x0 && a->x0 <= b->x1 && a->z1 >= b->z0 && a->z0 <= b->z1 &&
           a->y1 >= b->y0 && a->y0 <= b->y1;
}
static inline int box_isect_xz(const Ms116Box *a, int x0, int z0, int x1, int z1)
{
    return a->x1 >= x0 && a->x0 <= x1 && a->z1 >= z0 && a->z0 <= z1;
}
static inline int box_has(const Ms116Box *a, int x, int y, int z)
{
    return x >= a->x0 && x <= a->x1 && z >= a->z0 && z <= a->z1 && y >= a->y0 && y <= a->y1;
}
static inline void box_off(Ms116Box *a, int dy) { a->y0 += dy; a->y1 += dy; }

/* ───────────────────────── seeds ───────────────────────── */

static uint64_t carver_seed(uint64_t ws, int cx, int cz)
{
    uint64_t r;
    setSeed(&r, ws);
    uint64_t a = nextLong(&r);
    uint64_t b = nextLong(&r);
    return ((uint64_t)(int64_t)cx * a) ^ ((uint64_t)(int64_t)cz * b) ^ ws;
}

static uint64_t pop_seed(uint64_t ws, int bx, int bz)
{
    uint64_t r;
    setSeed(&r, ws);
    uint64_t a = nextLong(&r) | 1;
    uint64_t b = nextLong(&r) | 1;
    return ((uint64_t)(int64_t)bx * a + (uint64_t)(int64_t)bz * b) ^ ws;
}

/* 0 none, 1 normal (p = (double)0.004f), 2 mesa (p = 0.004) */
static int biome_ms_type(int id)
{
    switch (id) {
    case 8: case 9: case 40: case 41: case 42: case 43: case 127:
    case 170: case 171: case 172: case 173:
        return 0;
    case 37: case 38: case 39: case 165: case 166: case 167:
        return 2;
    default:
        return id < 0 ? 0 : 1;
    }
}

int ms116_has_start(const T116 *t, int cx, int cz)
{
    uint64_t r;
    setSeed(&r, carver_seed(t->seed, cx, cz));
    double d = nextDouble(&r);
    if (!(d < 0.004000000189989805))      /* both probabilities are >= 0.004: cheap reject */
        return 0;
    int ty = biome_ms_type(t116_biome4(&t->g, (cx << 2) + 2, (cz << 2) + 2));
    if (ty == 0) return 0;
    double p = ty == 2 ? 0.004 : (double)0.004f;
    return d < p ? ty : 0;
}

/* ───────────────────────── layout ───────────────────────── */

typedef struct {
    Ms116Start *s;
    uint64_t *r;
    int oom;
} Lay;

static int overlaps(const Ms116Start *s, const Ms116Box *b)
{
    for (int i = 0; i < s->n; i++)
        if (box_isect(&s->p[i].bb, b)) return 1;
    return 0;
}

static int add_piece(Lay *L, const Ms116Piece *pc)
{
    Ms116Start *s = L->s;
    if (s->n == s->cap) {
        int nc = s->cap ? s->cap * 2 : 64;
        Ms116Piece *np = realloc(s->p, nc * sizeof(*np));
        if (!np) { L->oom = 1; return -1; }
        s->p = np; s->cap = nc;
    }
    s->p[s->n] = *pc;
    return s->n++;
}

static void place_jigsaw(Lay *L, int idx);

/* MineshaftGenerator.getRandomJigsaw: returns a piece index or -1 */
static int random_jigsaw(Lay *L, int x, int y, int z, int dir, int depth)
{
    uint64_t *r = L->r;
    Ms116Piece pc;
    memset(&pc, 0, sizeof pc);
    pc.depth = depth;
    int m = nextInt(r, 100);
    if (m >= 80) {
        Ms116Box b = { x, y, z, x, y + 2, z };
        if (nextInt(r, 4) == 0) b.y1 += 4;
        switch (dir) {
        default:       b.x0 = x - 1; b.x1 = x + 3; b.z0 = z - 4; break;
        case MS116_S:  b.x0 = x - 1; b.x1 = x + 3; b.z1 = z + 4; break;
        case MS116_W:  b.x0 = x - 4; b.z0 = z - 1; b.z1 = z + 3; break;
        case MS116_E:  b.x1 = x + 4; b.z0 = z - 1; b.z1 = z + 3; break;
        }
        if (overlaps(L->s, &b)) return -1;
        pc.type = MS116_CROSSING; pc.facing = MS116_NONE; pc.dir = dir; pc.bb = b;
        pc.twofloors = (b.y1 - b.y0 + 1) > 3;
    } else if (m >= 70) {
        Ms116Box b = { x, y - 5, z, x, y + 2, z };
        switch (dir) {
        default:       b.x1 = x + 2; b.z0 = z - 8; break;
        case MS116_S:  b.x1 = x + 2; b.z1 = z + 8; break;
        case MS116_W:  b.x0 = x - 8; b.z1 = z + 2; break;
        case MS116_E:  b.x1 = x + 8; b.z1 = z + 2; break;
        }
        if (overlaps(L->s, &b)) return -1;
        pc.type = MS116_STAIRS; pc.facing = dir; pc.bb = b;
    } else {
        Ms116Box b = { x, y, z, x, y + 2, z };
        int l;
        for (l = nextInt(r, 3) + 2; l > 0; --l) {
            int mm = l * 5;
            switch (dir) {
            default:       b.x1 = x + 2; b.z0 = z - (mm - 1); break;
            case MS116_S:  b.x1 = x + 2; b.z1 = z + mm - 1; break;
            case MS116_W:  b.x0 = x - (mm - 1); b.z1 = z + 2; break;
            case MS116_E:  b.x1 = x + mm - 1; b.z1 = z + 2; break;
            }
            if (!overlaps(L->s, &b)) break;
        }
        if (l <= 0) return -1;
        pc.type = MS116_CORRIDOR; pc.facing = dir; pc.bb = b;
        pc.rails = nextInt(r, 3) == 0;
        pc.webs = !pc.rails && nextInt(r, 23) == 0;
        pc.length = (dir == MS116_N || dir == MS116_S) ? (b.z1 - b.z0 + 1) / 5 : (b.x1 - b.x0 + 1) / 5;
    }
    return add_piece(L, &pc);
}

/* MineshaftGenerator.tryGenerateJigsaw ("structurePiece" is always the room) */
static int try_jigsaw(Lay *L, int x, int y, int z, int dir, int depth)
{
    if (L->oom) return -1;
    if (depth > 8) return -1;
    const Ms116Box *rb = &L->s->p[0].bb;
    if (abs(x - rb->x0) > 80 || abs(z - rb->z0) > 80) return -1;
    int idx = random_jigsaw(L, x, y, z, dir, depth + 1);
    if (idx >= 0) place_jigsaw(L, idx);
    return idx;
}

static void place_room(Lay *L)
{
    uint64_t *r = L->r;
    Ms116Start *s = L->s;
    Ms116Box rb = s->p[0].bb;
    int i = s->p[0].depth;
    int cX = rb.x1 - rb.x0 + 1, cY = rb.y1 - rb.y0 + 1, cZ = rb.z1 - rb.z0 + 1;
    int j = cY - 3 - 1;
    if (j <= 0) j = 1;
    int k;
    for (k = 0; k < cX; k += 4) {
        k += nextInt(r, cX);
        if (k + 3 > cX) break;
        int y = rb.y0 + nextInt(r, j) + 1;
        int idx = try_jigsaw(L, rb.x0 + k, y, rb.z0 - 1, MS116_N, i);
        if (idx < 0 || s->nent >= 64) continue;
        Ms116Box b = s->p[idx].bb;
        s->ent[s->nent++] = (Ms116Box){ b.x0, b.y0, rb.z0, b.x1, b.y1, rb.z0 + 1 };
    }
    for (k = 0; k < cX; k += 4) {
        k += nextInt(r, cX);
        if (k + 3 > cX) break;
        int y = rb.y0 + nextInt(r, j) + 1;
        int idx = try_jigsaw(L, rb.x0 + k, y, rb.z1 + 1, MS116_S, i);
        if (idx < 0 || s->nent >= 64) continue;
        Ms116Box b = s->p[idx].bb;
        s->ent[s->nent++] = (Ms116Box){ b.x0, b.y0, rb.z1 - 1, b.x1, b.y1, rb.z1 };
    }
    for (k = 0; k < cZ; k += 4) {
        k += nextInt(r, cZ);
        if (k + 3 > cZ) break;
        int y = rb.y0 + nextInt(r, j) + 1;
        int idx = try_jigsaw(L, rb.x0 - 1, y, rb.z0 + k, MS116_W, i);
        if (idx < 0 || s->nent >= 64) continue;
        Ms116Box b = s->p[idx].bb;
        s->ent[s->nent++] = (Ms116Box){ rb.x0, b.y0, b.z0, rb.x0 + 1, b.y1, b.z1 };
    }
    for (k = 0; k < cZ; k += 4) {
        k += nextInt(r, cZ);
        if (k + 3 > cZ) break;
        int y = rb.y0 + nextInt(r, j) + 1;
        int idx = try_jigsaw(L, rb.x1 + 1, y, rb.z0 + k, MS116_E, i);
        if (idx < 0 || s->nent >= 64) continue;
        Ms116Box b = s->p[idx].bb;
        s->ent[s->nent++] = (Ms116Box){ rb.x1 - 1, b.y0, b.z0, rb.x1, b.y1, b.z1 };
    }
}

static void place_corridor(Lay *L, int idx)
{
    uint64_t *r = L->r;
    Ms116Piece pc = L->s->p[idx];     /* copy: the array may move */
    Ms116Box b = pc.bb;
    int i = pc.depth;
    int j = nextInt(r, 4);
    int y;
    switch (pc.facing) {
    default: /* NORTH */
        if (j <= 1) { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x0, y, b.z0 - 1, pc.facing, i); }
        else if (j == 2) { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x0 - 1, y, b.z0, MS116_W, i); }
        else { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x1 + 1, y, b.z0, MS116_E, i); }
        break;
    case MS116_S:
        if (j <= 1) { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x0, y, b.z1 + 1, pc.facing, i); }
        else if (j == 2) { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x0 - 1, y, b.z1 - 3, MS116_W, i); }
        else { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x1 + 1, y, b.z1 - 3, MS116_E, i); }
        break;
    case MS116_W:
        if (j <= 1) { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x0 - 1, y, b.z0, pc.facing, i); }
        else if (j == 2) { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x0, y, b.z0 - 1, MS116_N, i); }
        else { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x0, y, b.z1 + 1, MS116_S, i); }
        break;
    case MS116_E:
        if (j <= 1) { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x1 + 1, y, b.z0, pc.facing, i); }
        else if (j == 2) { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x1 - 3, y, b.z0 - 1, MS116_N, i); }
        else { y = b.y0 - 1 + nextInt(r, 3); try_jigsaw(L, b.x1 - 3, y, b.z1 + 1, MS116_S, i); }
        break;
    }
    if (i >= 8) return;
    if (pc.facing == MS116_N || pc.facing == MS116_S) {
        for (int k = b.z0 + 3; k + 3 <= b.z1; k += 5) {
            int l = nextInt(r, 5);
            if (l == 0) try_jigsaw(L, b.x0 - 1, b.y0, k, MS116_W, i + 1);
            else if (l == 1) try_jigsaw(L, b.x1 + 1, b.y0, k, MS116_E, i + 1);
        }
    } else {
        for (int m = b.x0 + 3; m + 3 <= b.x1; m += 5) {
            int n = nextInt(r, 5);
            if (n == 0) try_jigsaw(L, m, b.y0, b.z0 - 1, MS116_N, i + 1);
            else if (n == 1) try_jigsaw(L, m, b.y0, b.z1 + 1, MS116_S, i + 1);
        }
    }
}

static void place_crossing(Lay *L, int idx)
{
    uint64_t *r = L->r;
    Ms116Piece pc = L->s->p[idx];
    Ms116Box b = pc.bb;
    int i = pc.depth;
    switch (pc.dir) {
    default: /* NORTH */
        try_jigsaw(L, b.x0 + 1, b.y0, b.z0 - 1, MS116_N, i);
        try_jigsaw(L, b.x0 - 1, b.y0, b.z0 + 1, MS116_W, i);
        try_jigsaw(L, b.x1 + 1, b.y0, b.z0 + 1, MS116_E, i);
        break;
    case MS116_S:
        try_jigsaw(L, b.x0 + 1, b.y0, b.z1 + 1, MS116_S, i);
        try_jigsaw(L, b.x0 - 1, b.y0, b.z0 + 1, MS116_W, i);
        try_jigsaw(L, b.x1 + 1, b.y0, b.z0 + 1, MS116_E, i);
        break;
    case MS116_W:
        try_jigsaw(L, b.x0 + 1, b.y0, b.z0 - 1, MS116_N, i);
        try_jigsaw(L, b.x0 + 1, b.y0, b.z1 + 1, MS116_S, i);
        try_jigsaw(L, b.x0 - 1, b.y0, b.z0 + 1, MS116_W, i);
        break;
    case MS116_E:
        try_jigsaw(L, b.x0 + 1, b.y0, b.z0 - 1, MS116_N, i);
        try_jigsaw(L, b.x0 + 1, b.y0, b.z1 + 1, MS116_S, i);
        try_jigsaw(L, b.x1 + 1, b.y0, b.z0 + 1, MS116_E, i);
        break;
    }
    if (pc.twofloors) {
        if (next(r, 1)) try_jigsaw(L, b.x0 + 1, b.y0 + 4, b.z0 - 1, MS116_N, i);
        if (next(r, 1)) try_jigsaw(L, b.x0 - 1, b.y0 + 4, b.z0 + 1, MS116_W, i);
        if (next(r, 1)) try_jigsaw(L, b.x1 + 1, b.y0 + 4, b.z0 + 1, MS116_E, i);
        if (next(r, 1)) try_jigsaw(L, b.x0 + 1, b.y0 + 4, b.z1 + 1, MS116_S, i);
    }
}

static void place_stairs(Lay *L, int idx)
{
    Ms116Piece pc = L->s->p[idx];
    Ms116Box b = pc.bb;
    int i = pc.depth;
    switch (pc.facing) {
    default:      try_jigsaw(L, b.x0, b.y0, b.z0 - 1, MS116_N, i); break;
    case MS116_S: try_jigsaw(L, b.x0, b.y0, b.z1 + 1, MS116_S, i); break;
    case MS116_W: try_jigsaw(L, b.x0 - 1, b.y0, b.z0, MS116_W, i); break;
    case MS116_E: try_jigsaw(L, b.x1 + 1, b.y0, b.z0, MS116_E, i); break;
    }
}

static void place_jigsaw(Lay *L, int idx)
{
    switch (L->s->p[idx].type) {
    case MS116_CORRIDOR: place_corridor(L, idx); break;
    case MS116_CROSSING: place_crossing(L, idx); break;
    case MS116_STAIRS:   place_stairs(L, idx); break;
    default: break;
    }
}

void ms116_start_free(Ms116Start *s)
{
    free(s->p);
    s->p = NULL;
    s->n = s->cap = 0;
}

static int ms116_start_build(const T116 *t, int cx, int cz, int ty, Ms116Start *s);

/* (10/10) the layout only depends on the 48 low bits (carver seed) and on the
 * type (mesa: no random vertical shift): cached per thread, so the 65 536
 * sister seeds of a structure seed and the many chunks asking for the same
 * start build it once. Returns a deep copy (same result as building it). */
typedef struct { uint64_t low; int cx, cz, ty, ok; Ms116Start s; } MsCacheE;
static __thread MsCacheE *ms_cache = NULL;

static int ms_copy(Ms116Start *dst, const Ms116Start *src)
{
    *dst = *src;
    dst->p = NULL;
    if (src->n > 0) {
        dst->p = (Ms116Piece *)malloc(sizeof(Ms116Piece) * (size_t)src->n);
        if (!dst->p) { memset(dst, 0, sizeof(*dst)); return 0; }
        memcpy(dst->p, src->p, sizeof(Ms116Piece) * (size_t)src->n);
    }
    dst->cap = src->n;
    return 1;
}

int ms116_start(const T116 *t, int cx, int cz, Ms116Start *s)
{
    memset(s, 0, sizeof(*s));
    int ty = ms116_has_start(t, cx, cz);
    if (!ty) return 0;
    uint64_t low = t->seed & 0xFFFFFFFFFFFFULL;
    if (!ms_cache) ms_cache = (MsCacheE *)calloc(64, sizeof(MsCacheE));
    MsCacheE *e = ms_cache ? &ms_cache[((unsigned)cx * 31u + (unsigned)cz * 17u + (unsigned)ty) & 63] : NULL;
    if (e && e->ok && e->low == low && e->cx == cx && e->cz == cz && e->ty == ty)
        return ms_copy(s, &e->s);
    if (!ms116_start_build(t, cx, cz, ty, s)) return 0;
    if (e) {
        if (e->ok) ms116_start_free(&e->s);
        e->ok = 0;
        if (ms_copy(&e->s, s)) { e->low = low; e->cx = cx; e->cz = cz; e->ty = ty; e->ok = 1; }
    }
    return 1;
}

static int ms116_start_build(const T116 *t, int cx, int cz, int ty, Ms116Start *s)
{
    s->cx = cx; s->cz = cz; s->mesa = ty == 2;
    uint64_t r;
    setSeed(&r, carver_seed(t->seed, cx, cz));
    Lay L = { s, &r, 0 };
    Ms116Piece room;
    memset(&room, 0, sizeof room);
    room.type = MS116_ROOM; room.facing = MS116_NONE; room.depth = 0;
    int x = (cx << 4) + 2, z = (cz << 4) + 2;
    int ax = nextInt(&r, 6);
    int ay = nextInt(&r, 6);
    int az = nextInt(&r, 6);
    room.bb = (Ms116Box){ x, 50, z, x + 7 + ax, 54 + ay, z + 7 + az };
    if (add_piece(&L, &room) < 0) { ms116_start_free(s); return 0; }
    place_room(&L);
    if (L.oom) { ms116_start_free(s); return 0; }

    Ms116Box bb = { 0x7fffffff, 0x7fffffff, 0x7fffffff, -0x7fffffff - 1, -0x7fffffff - 1, -0x7fffffff - 1 };
    for (int i = 0; i < s->n; i++) {
        const Ms116Box *b = &s->p[i].bb;
        if (b->x0 < bb.x0) bb.x0 = b->x0;
        if (b->y0 < bb.y0) bb.y0 = b->y0;
        if (b->z0 < bb.z0) bb.z0 = b->z0;
        if (b->x1 > bb.x1) bb.x1 = b->x1;
        if (b->y1 > bb.y1) bb.y1 = b->y1;
        if (b->z1 > bb.z1) bb.z1 = b->z1;
    }
    int dy;
    int cy = bb.y1 - bb.y0 + 1;
    if (s->mesa) {
        dy = SEA_LEVEL - bb.y1 + cy / 2 + 5;
    } else {
        int k = SEA_LEVEL - 10;
        int l = cy + 1;
        if (l < k) l += nextInt(&r, k - l);
        dy = l - bb.y1;
    }
    box_off(&bb, dy);
    for (int i = 0; i < s->n; i++) box_off(&s->p[i].bb, dy);
    for (int i = 0; i < s->nent; i++) box_off(&s->ent[i], dy);
    s->bb = bb;
    return 1;
}

/* ───────────────────────── world access ───────────────────────── */

typedef struct {
    void *ctx;
    Ms116Get get;
    Ms116Set set;
    Ms116Box cb;          /* the chunk box: x, z of the chunk, y 1..512 */
    int x0, z0;
    short top[256];       /* OCEAN_FLOOR_WG heightmap (highest blocking y + 1), -1 unknown */
    uint64_t *r;
} W;

/* Material.blocksMovement() */
static inline int blocks_movement(int b)
{
    return T116_IS_SOLID(b);
}

/* BlockState.isOpaqueFullCube() */
static inline int opaque_full(int b)
{
    switch (b) {
    case T116_AIR: case T116_CAVE_AIR: case T116_WATER: case T116_LAVA:
    case T116_ICE: case T116_SPAWNER: case T116_CHEST: case T116_FENCE:
    case T116_RAIL: case T116_COBWEB: case T116_TORCH:
        return 0;
    default:
        return 1;
    }
}

static inline int wget(W *w, int x, int y, int z)
{
    if (y < 0 || y > 255) return T116_AIR;
    return w->get(w->ctx, x, y, z);
}

static int top_of(W *w, int x, int z)
{
    int i = ((z - w->z0) << 4) | (x - w->x0);
    if (w->top[i] < 0) {
        int y;
        for (y = 255; y >= 0; y--)
            if (blocks_movement(w->get(w->ctx, x, y, z))) break;
        w->top[i] = (short)(y + 1);
    }
    return w->top[i];
}

/* During population the chunk is still at LIQUID_CARVERS, whose heightmap types are
 * the post-carver ones: OCEAN_FLOOR_WG is NOT updated by feature / structure writes. */
static void wset(W *w, int x, int y, int z, int b)
{
    if (y < 0 || y > 255) return;
    w->set(w->ctx, x, y, z, b);
}

/* ───────────────────────── piece helpers (StructurePiece) ───────────────────────── */

static inline int tx(const Ms116Piece *p, int x, int z)
{
    switch (p->facing) {
    case MS116_N: case MS116_S: return p->bb.x0 + x;
    case MS116_W: return p->bb.x1 - z;
    case MS116_E: return p->bb.x0 + z;
    default: return x;
    }
}
static inline int ty_(const Ms116Piece *p, int y)
{
    return p->facing == MS116_NONE ? y : y + p->bb.y0;
}
static inline int tz(const Ms116Piece *p, int x, int z)
{
    switch (p->facing) {
    case MS116_N: return p->bb.z1 - z;
    case MS116_S: return p->bb.z0 + z;
    case MS116_W: case MS116_E: return p->bb.z0 + x;
    default: return z;
    }
}

static void add_block(W *w, const Ms116Piece *p, int b, int x, int y, int z)
{
    int X = tx(p, x, z), Y = ty_(p, y), Z = tz(p, x, z);
    if (!box_has(&w->cb, X, Y, Z)) return;
    wset(w, X, Y, Z, b);
}

static int get_block_at(W *w, const Ms116Piece *p, int x, int y, int z)
{
    int X = tx(p, x, z), Y = ty_(p, y), Z = tz(p, x, z);
    if (!box_has(&w->cb, X, Y, Z)) return T116_AIR;
    return wget(w, X, Y, Z);
}

static int under_sea(W *w, const Ms116Piece *p, int x, int y, int z)
{
    int X = tx(p, x, z), Y = ty_(p, y + 1), Z = tz(p, x, z);
    if (!box_has(&w->cb, X, Y, Z)) return 0;
    return Y < top_of(w, X, Z);
}

static void fill(W *w, const Ms116Piece *p, int x0, int y0, int z0, int x1, int y1, int z1,
                 int outl, int ins, int skip_air)
{
    for (int y = y0; y <= y1; ++y)
        for (int x = x0; x <= x1; ++x)
            for (int z = z0; z <= z1; ++z) {
                if (skip_air && T116_IS_AIR(get_block_at(w, p, x, y, z))) continue;
                if (y == y0 || y == y1 || x == x0 || x == x1 || z == z0 || z == z1)
                    add_block(w, p, outl, x, y, z);
                else
                    add_block(w, p, ins, x, y, z);
            }
}

static void fill_under_sea(W *w, const Ms116Piece *p, float f, int x0, int y0, int z0, int x1, int y1, int z1,
                           int outl, int ins, int skip_air, int need_sea)
{
    for (int y = y0; y <= y1; ++y)
        for (int x = x0; x <= x1; ++x)
            for (int z = z0; z <= z1; ++z) {
                if (nextFloat(w->r) > f) continue;
                if (skip_air && T116_IS_AIR(get_block_at(w, p, x, y, z))) continue;
                if (need_sea && !under_sea(w, p, x, y, z)) continue;
                if (y == y0 || y == y1 || x == x0 || x == x1 || z == z0 || z == z1)
                    add_block(w, p, outl, x, y, z);
                else
                    add_block(w, p, ins, x, y, z);
            }
}

static void add_random(W *w, const Ms116Piece *p, float f, int x, int y, int z, int b)
{
    if (nextFloat(w->r) < f) add_block(w, p, b, x, y, z);
}

#if defined(__GNUC__) && !defined(__clang__)
__attribute__((optimize("fp-contract=off")))
#endif
static void ellipsoid_air(W *w, const Ms116Piece *p, int minX, int minY, int minZ, int maxX, int maxY, int maxZ)
{
    float f = (float)(maxX - minX + 1);
    float g = (float)(maxY - minY + 1);
    float h = (float)(maxZ - minZ + 1);
    float ci = (float)minX + f / 2.0f;
    float cj = (float)minZ + h / 2.0f;
    for (int k = minY; k <= maxY; ++k) {
        volatile float l = (float)(k - minY) / g;
        for (int m = minX; m <= maxX; ++m) {
            volatile float n = ((float)m - ci) / (f * 0.5f);
            for (int o = minZ; o <= maxZ; ++o) {
                volatile float pp = ((float)o - cj) / (h * 0.5f);
                volatile float a = n * n;
                volatile float b = l * l;
                volatile float c = pp * pp;
                volatile float s1 = a + b;
                volatile float q = s1 + c;
                if (!(q <= 1.05f)) continue;
                add_block(w, p, T116_CAVE_AIR, m, k, o);
            }
        }
    }
}

/* StructurePiece.method_14937: any liquid on the faces of the piece box (+1), clipped to the chunk box */
static int liquid_around(W *w, const Ms116Piece *pc)
{
    const Ms116Box *b = &pc->bb, *c = &w->cb;
    int i = b->x0 - 1 > c->x0 ? b->x0 - 1 : c->x0;
    int j = b->y0 - 1 > c->y0 ? b->y0 - 1 : c->y0;
    int k = b->z0 - 1 > c->z0 ? b->z0 - 1 : c->z0;
    int l = b->x1 + 1 < c->x1 ? b->x1 + 1 : c->x1;
    int m = b->y1 + 1 < c->y1 ? b->y1 + 1 : c->y1;
    int n = b->z1 + 1 < c->z1 ? b->z1 + 1 : c->z1;
    for (int o = i; o <= l; ++o)
        for (int q = k; q <= n; ++q) {
            if (T116_IS_LIQUID(wget(w, o, j, q))) return 1;
            if (T116_IS_LIQUID(wget(w, o, m, q))) return 1;
        }
    for (int o = i; o <= l; ++o)
        for (int y = j; y <= m; ++y) {
            if (T116_IS_LIQUID(wget(w, o, y, k))) return 1;
            if (T116_IS_LIQUID(wget(w, o, y, n))) return 1;
        }
    for (int q = k; q <= n; ++q)
        for (int y = j; y <= m; ++y) {
            if (T116_IS_LIQUID(wget(w, i, y, q))) return 1;
            if (T116_IS_LIQUID(wget(w, l, y, q))) return 1;
        }
    return 0;
}

/* ───────────────────────── generate() of each piece ───────────────────────── */

static int gen_room(W *w, const Ms116Start *s, const Ms116Piece *p)
{
    if (liquid_around(w, p)) return 0;
    const Ms116Box *b = &p->bb;
    fill(w, p, b->x0, b->y0, b->z0, b->x1, b->y0, b->z1, T116_DIRT, T116_CAVE_AIR, 1);
    int ym = b->y0 + 3 < b->y1 ? b->y0 + 3 : b->y1;
    fill(w, p, b->x0, b->y0 + 1, b->z0, b->x1, ym, b->z1, T116_CAVE_AIR, T116_CAVE_AIR, 0);
    for (int e = 0; e < s->nent; e++) {
        const Ms116Box *E = &s->ent[e];
        fill(w, p, E->x0, E->y1 - 2, E->z0, E->x1, E->y1, E->z1, T116_CAVE_AIR, T116_CAVE_AIR, 0);
    }
    ellipsoid_air(w, p, b->x0, b->y0 + 4, b->z0, b->x1, b->y1, b->z1);
    return 1;
}

static int gen_stairs(W *w, const Ms116Piece *p)
{
    if (liquid_around(w, p)) return 0;
    fill(w, p, 0, 5, 0, 2, 7, 1, T116_CAVE_AIR, T116_CAVE_AIR, 0);
    fill(w, p, 0, 0, 7, 2, 2, 8, T116_CAVE_AIR, T116_CAVE_AIR, 0);
    for (int i = 0; i < 5; ++i)
        fill(w, p, 0, 5 - i - (i < 4 ? 1 : 0), 2 + i, 2, 7 - i, 2 + i, T116_CAVE_AIR, T116_CAVE_AIR, 0);
    return 1;
}

static void crossing_pillar(W *w, const Ms116Piece *p, int i, int j, int k, int l)
{
    if (!T116_IS_AIR(get_block_at(w, p, i, l + 1, k)))
        fill(w, p, i, j, k, i, l, k, T116_PLANKS, T116_CAVE_AIR, 0);
}

static int gen_crossing(W *w, const Ms116Piece *p)
{
    if (liquid_around(w, p)) return 0;
    const Ms116Box *b = &p->bb;
    const int A = T116_CAVE_AIR;
    if (p->twofloors) {
        fill(w, p, b->x0 + 1, b->y0, b->z0, b->x1 - 1, b->y0 + 2, b->z1, A, A, 0);
        fill(w, p, b->x0, b->y0, b->z0 + 1, b->x1, b->y0 + 2, b->z1 - 1, A, A, 0);
        fill(w, p, b->x0 + 1, b->y1 - 2, b->z0, b->x1 - 1, b->y1, b->z1, A, A, 0);
        fill(w, p, b->x0, b->y1 - 2, b->z0 + 1, b->x1, b->y1, b->z1 - 1, A, A, 0);
        fill(w, p, b->x0 + 1, b->y0 + 3, b->z0 + 1, b->x1 - 1, b->y0 + 3, b->z1 - 1, A, A, 0);
    } else {
        fill(w, p, b->x0 + 1, b->y0, b->z0, b->x1 - 1, b->y1, b->z1, A, A, 0);
        fill(w, p, b->x0, b->y0, b->z0 + 1, b->x1, b->y1, b->z1 - 1, A, A, 0);
    }
    crossing_pillar(w, p, b->x0 + 1, b->y0, b->z0 + 1, b->y1);
    crossing_pillar(w, p, b->x0 + 1, b->y0, b->z1 - 1, b->y1);
    crossing_pillar(w, p, b->x1 - 1, b->y0, b->z0 + 1, b->y1);
    crossing_pillar(w, p, b->x1 - 1, b->y0, b->z1 - 1, b->y1);
    for (int i = b->x0; i <= b->x1; ++i)
        for (int j = b->z0; j <= b->z1; ++j) {
            if (!T116_IS_AIR(get_block_at(w, p, i, b->y0 - 1, j)) || !under_sea(w, p, i, b->y0 - 1, j)) continue;
            add_block(w, p, T116_PLANKS, i, b->y0 - 1, j);
        }
    return 1;
}

/* MineshaftCorridor.addChest: rail + chest minecart (entity) */
static void corridor_chest(W *w, const Ms116Piece *p, int x, int y, int z)
{
    int X = tx(p, x, z), Y = ty_(p, y), Z = tz(p, x, z);
    if (box_has(&w->cb, X, Y, Z) && T116_IS_AIR(wget(w, X, Y, Z)) && !T116_IS_AIR(wget(w, X, Y - 1, Z))) {
        (void)next(w->r, 1);              /* rail shape */
        add_block(w, p, T116_RAIL, x, y, z);
        (void)nextLong(w->r);             /* loot table seed */
    }
}

static void corridor_support(W *w, const Ms116Piece *p, int i, int j, int k, int l, int m)
{
    /* method_14719 */
    for (int x = i; x <= m; ++x)
        if (T116_IS_AIR(get_block_at(w, p, x, l + 1, k))) return;
    fill(w, p, i, j, k, i, l - 1, k, T116_FENCE, T116_CAVE_AIR, 0);
    fill(w, p, m, j, k, m, l - 1, k, T116_FENCE, T116_CAVE_AIR, 0);
    if (nextInt(w->r, 4) == 0) {
        fill(w, p, i, l, k, i, l, k, T116_PLANKS, T116_CAVE_AIR, 0);
        fill(w, p, m, l, k, m, l, k, T116_PLANKS, T116_CAVE_AIR, 0);
    } else {
        fill(w, p, i, l, k, m, l, k, T116_PLANKS, T116_CAVE_AIR, 0);
        add_random(w, p, 0.05f, i + 1, l, k - 1, T116_TORCH);
        add_random(w, p, 0.05f, i + 1, l, k + 1, T116_TORCH);
    }
}

static void corridor_web(W *w, const Ms116Piece *p, float f, int x, int y, int z)
{
    if (under_sea(w, p, x, y, z)) add_random(w, p, f, x, y, z, T116_COBWEB);
}

static int gen_corridor(W *w, const Ms116Piece *p)
{
    if (liquid_around(w, p)) return 0;
    const int A = T116_CAVE_AIR;
    int m = p->length * 5 - 1;
    int has_spawner = 0;
    fill(w, p, 0, 0, 0, 2, 1, m, A, A, 0);
    fill_under_sea(w, p, 0.8f, 0, 2, 0, 2, 2, m, A, A, 0, 0);
    if (p->webs)
        fill_under_sea(w, p, 0.6f, 0, 0, 0, 2, 1, m, T116_COBWEB, A, 0, 1);
    for (int n = 0; n < p->length; ++n) {
        int o = 2 + n * 5;
        corridor_support(w, p, 0, 0, o, 2, 2);
        corridor_web(w, p, 0.1f, 0, 2, o - 1);
        corridor_web(w, p, 0.1f, 2, 2, o - 1);
        corridor_web(w, p, 0.1f, 0, 2, o + 1);
        corridor_web(w, p, 0.1f, 2, 2, o + 1);
        corridor_web(w, p, 0.05f, 0, 2, o - 2);
        corridor_web(w, p, 0.05f, 2, 2, o - 2);
        corridor_web(w, p, 0.05f, 0, 2, o + 2);
        corridor_web(w, p, 0.05f, 2, 2, o + 2);
        if (nextInt(w->r, 100) == 0) corridor_chest(w, p, 2, 0, o - 1);
        if (nextInt(w->r, 100) == 0) corridor_chest(w, p, 0, 0, o + 1);
        if (!p->webs || has_spawner) continue;
        int Y = ty_(p, 0);
        int q = o - 1 + nextInt(w->r, 3);
        int X = tx(p, 1, q), Z = tz(p, 1, q);
        if (!box_has(&w->cb, X, Y, Z) || !under_sea(w, p, 1, 0, q)) continue;
        has_spawner = 1;
        wset(w, X, Y, Z, T116_SPAWNER);
    }
    for (int t = 0; t <= 2; ++t)
        for (int u = 0; u <= m; ++u) {
            if (!T116_IS_AIR(get_block_at(w, p, t, -1, u)) || !under_sea(w, p, t, -1, u)) continue;
            add_block(w, p, T116_PLANKS, t, -1, u);
        }
    if (p->rails) {
        for (int x = 0; x <= m; ++x) {
            int b = get_block_at(w, p, 1, -1, x);
            if (T116_IS_AIR(b) || !opaque_full(b)) continue;
            float f = under_sea(w, p, 1, 0, x) ? 0.7f : 0.9f;
            add_random(w, p, f, 1, 0, x, T116_RAIL);
        }
    }
    return 1;
}

/* ───────────────────────── population ───────────────────────── */

/* fastutil 8.2.1 LongOpenHashSet (default capacity 16, load factor .75): insertion
 * and iteration order (null key first, then slots from the top down). */
typedef struct {
    uint64_t *key;
    int n, mask, size, maxfill, has_null;
} LSet;

static inline int fu_mix(uint64_t x)
{
    uint64_t h = x * 0x9E3779B97F4A7C15ULL;
    h ^= h >> 32;
    return (int)(uint32_t)(h ^ (h >> 16));
}

static int lset_init(LSet *s)
{
    s->n = 32; s->mask = 31; s->size = 0; s->maxfill = 24; s->has_null = 0;
    s->key = calloc(s->n + 1, sizeof(uint64_t));
    return s->key != NULL;
}

static int lset_add(LSet *s, uint64_t k)
{
    if (k == 0) {
        if (s->has_null) return 1;
        s->has_null = 1;
    } else {
        int pos = fu_mix(k) & s->mask;
        while (s->key[pos] != 0) {
            if (s->key[pos] == k) return 1;
            pos = (pos + 1) & s->mask;
        }
        s->key[pos] = k;
    }
    if (s->size++ >= s->maxfill) {
        /* rehash(arraySize(size + 1, .75f)) */
        int need = (int)((s->size + 1) / 0.75f + 0.999999f);
        long nn = 1;
        while (nn < need) nn <<= 1;
        if (nn < 2) nn = 2;
        int newN = (int)nn, mask = newN - 1;
        uint64_t *nk = calloc(newN + 1, sizeof(uint64_t));
        if (!nk) return 0;
        int i = s->n;
        for (int j = s->size - (s->has_null ? 1 : 0); j-- != 0;) {
            while (s->key[--i] == 0);
            int pos = fu_mix(s->key[i]) & mask;
            while (nk[pos] != 0) pos = (pos + 1) & mask;
            nk[pos] = s->key[i];
        }
        free(s->key);
        s->key = nk; s->n = newN; s->mask = mask;
        long mf = (long)((double)newN * 0.75f + 0.999999);
        s->maxfill = (int)(mf < newN - 1 ? mf : newN - 1);
    }
    return 1;
}

static int ms_populate_impl(const T116 *t, int cx, int cz, const int16_t *hm, W *w)
{
    int bx = cx << 4, bz = cz << 4;
    LSet set;
    if (!lset_init(&set)) return -1;
    /* the starts reaching the chunk, in ChunkGenerator.addStructureReferences order */
    Ms116Start st[32];
    int ns = 0;
    int pos_of[17 * 17];
    for (int x = cx - 8; x <= cx + 8; ++x)
        for (int z = cz - 8; z <= cz + 8; ++z) {
            pos_of[(x - cx + 8) * 17 + (z - cz + 8)] = -1;
            if (!ms116_has_start(t, x, z)) continue;
            if (ns >= 32) continue;
            if (!ms116_start(t, x, z, &st[ns])) continue;
            if (!box_isect_xz(&st[ns].bb, bx, bz, bx + 15, bz + 15)) { ms116_start_free(&st[ns]); continue; }
            pos_of[(x - cx + 8) * 17 + (z - cz + 8)] = ns;
            uint64_t key = (uint64_t)(uint32_t)x | ((uint64_t)(uint32_t)z << 32);
            lset_add(&set, key);
            ns++;
        }
    int order[64], no = 0;
    uint64_t keys[64];
    int nk = 0;
    if (set.has_null) keys[nk++] = 0;
    for (int pos = set.n - 1; pos >= 0; --pos)
        if (set.key[pos] != 0 && nk < 64) keys[nk++] = set.key[pos];
    for (int i = 0; i < nk; i++) {
        int x = (int)(uint32_t)keys[i], z = (int)(uint32_t)(keys[i] >> 32);
        int id = pos_of[(x - cx + 8) * 17 + (z - cz + 8)];
        if (id >= 0) order[no++] = id;
    }
    free(set.key);

    uint64_t r;
    setSeed(&r, pop_seed(t->seed, bx, bz) + 0 + 10000 * 3);
    w->r = &r;
    w->cb = (Ms116Box){ bx, 1, bz, bx + 15, 512, bz + 15 };
    w->x0 = bx; w->z0 = bz;
    for (int i = 0; i < 256; i++) w->top[i] = hm ? hm[i] : -1;
    if (!hm && no > 0)            /* from the world before any write */
        for (int z = 0; z < 16; z++)
            for (int x = 0; x < 16; x++) top_of(w, bx + x, bz + z);

    int count = 0;
    for (int oi = 0; oi < no; oi++) {
        const Ms116Start *s = &st[order[oi]];
        for (int i = 0; i < s->n; i++) {
            const Ms116Piece *p = &s->p[i];
            if (!box_isect(&p->bb, &w->cb)) continue;
            int ok = 0;
            switch (p->type) {
            case MS116_ROOM:     ok = gen_room(w, s, p); break;
            case MS116_CORRIDOR: ok = gen_corridor(w, p); break;
            case MS116_CROSSING: ok = gen_crossing(w, p); break;
            case MS116_STAIRS:   ok = gen_stairs(w, p); break;
            }
            count += ok;
        }
    }
    for (int i = 0; i < ns; i++) ms116_start_free(&st[i]);
    return count;
}

int ms116_populate(const T116 *t, int cx, int cz, const int16_t *ocean_floor_wg,
                   void *ctx, Ms116Get get, Ms116Set set)
{
    W w;
    w.ctx = ctx; w.get = get; w.set = set;
    return ms_populate_impl(t, cx, cz, ocean_floor_wg, &w);
}

void ms116_heightmap(const T116Chunk *c, int16_t out[256])
{
    for (int z = 0; z < 16; z++)
        for (int x = 0; x < 16; x++) {
            int y;
            for (y = 255; y >= 0; y--)
                if (blocks_movement(T116_AT(c, x, y, z))) break;
            out[z * 16 + x] = (int16_t)(y + 1);
        }
}

static int chunk_get(void *ctx, int x, int y, int z)
{
    T116Chunk *c = ctx;
    return T116_AT(c, x & 15, y, z & 15);
}
static void chunk_set(void *ctx, int x, int y, int z, int b)
{
    T116Chunk *c = ctx;
    T116_AT(c, x & 15, y, z & 15) = (uint8_t)b;
}

int ms116_populate_chunk(const T116 *t, T116Chunk *c, const T116Chunk *base)
{
    int16_t hm[256];
    if (base) ms116_heightmap(base, hm);
    return ms116_populate(t, c->cx, c->cz, base ? hm : NULL, c, chunk_get, chunk_set);
}

int ms116_any_piece_in(const T116 *t, int x1, int y1, int z1, int x2, int y2, int z2)
{
    Ms116Box q = { x1, y1, z1, x2, y2, z2 };
    int cx0 = (x1 >> 4) - 8, cx1 = (x2 >> 4) + 8;
    int cz0 = (z1 >> 4) - 8, cz1 = (z2 >> 4) + 8;
    for (int x = cx0; x <= cx1; ++x)
        for (int z = cz0; z <= cz1; ++z) {
            if (!ms116_has_start(t, x, z)) continue;
            Ms116Start s;
            if (!ms116_start(t, x, z, &s)) continue;
            int hit = 0;
            if (box_isect(&s.bb, &q))
                for (int i = 0; i < s.n && !hit; i++)
                    hit = box_isect(&s.p[i].bb, &q);
            ms116_start_free(&s);
            if (hit) return 1;
        }
    return 0;
}
