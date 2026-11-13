/*
 * jigsaw_engine.c — (31/10) JigsawPlacement.addPieces in C: a line-by-line port of
 * jigsaw.generate (villages, pillager outposts, abandoned camps, bastions, ancient
 * cities; 1.16.1 → 26.3), checked piece for piece against the Python version.
 *
 * The data (pools, elements, templates, jigsaw blocks) is loaded once per data set
 * by jigsaw.py (ssa_jig_load), names interned as integers. Heights:
 *   mode 0  none (rigid structures at a fixed Y: bastions, ancient cities)
 *   mode 1  1.18+ getFirstFreeHeight(WORLD_SURFACE_WG) = terrain library cv_surface + 1
 *   mode 2  1.16 / 1.17: ssa_t116_base_height (the exact 1.16 noise terrain)
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "rng.h"
#include "finders.h"

#ifdef _WIN32
  #define JEXPORT __declspec(dllexport)
  #define JTLS __declspec(thread)
#else
  #define JEXPORT __attribute__((visibility("default")))
  #define JTLS __thread
#endif

extern int ssa_t116_base_height(int mc, uint64_t seed, int32_t x, int32_t z);
extern int ssa_tf_surface(int mc, uint64_t seed, int x, int z);      /* session_engine.c */

#define J_MIN_Y (-64)
#define J_MAX_Y 319

/* directions: 0 north, 1 east, 2 south, 3 west, 4 up, 5 down */
static const int DX[6] = {0, 1, 0, -1, 0, 0}, DY[6] = {0, 0, 0, 0, 1, -1}, DZ[6] = {-1, 0, 1, 0, 0, 0};
static const int OPP[6] = {2, 3, 0, 1, 5, 4};

typedef struct {          /* a jigsaw block of a template (local) */
    int x, y, z, front, top, name, target, pool, joint, pp, sp;
} JTJig;

typedef struct { int sx, sy, sz, j0, nj; } JTpl;
typedef struct { int kind, tpl, rigid, c0, nc; } JElem;   /* kind 0 single, 1 feature, 2 empty, 3 list */
typedef struct { int e0, ne, fallback, valid, maxsize; } JPool;

typedef struct {
    int ntpl, nelem, npool, njig, nlist;
    JTpl *tpl; JElem *el; JPool *pool; JTJig *jig;
    int *poolel;           /* the pools' expanded (weighted) element ids */
    int *listel;           /* the list elements' children */
    int feat_name, feat_target, feat_pool;   /* the feature's jigsaw (26.3: name -1) */
} JData;

JEXPORT void *ssa_jig_load(int ntpl, const int32_t *tpl, int njig, const int32_t *jig,
                           int nelem, const int32_t *el, int nlist, const int32_t *listel,
                           int npool, const int32_t *pool, int npe, const int32_t *poolel,
                           int feat_name, int feat_target, int feat_pool)
{
    JData *d = (JData *)calloc(1, sizeof(JData));
    if (!d) return NULL;
    d->ntpl = ntpl; d->njig = njig; d->nelem = nelem; d->nlist = nlist; d->npool = npool;
    d->tpl = (JTpl *)malloc(sizeof(JTpl) * (ntpl + 1));
    d->jig = (JTJig *)malloc(sizeof(JTJig) * (njig + 1));
    d->el = (JElem *)malloc(sizeof(JElem) * (nelem + 1));
    d->listel = (int *)malloc(sizeof(int) * (nlist + 1));
    d->pool = (JPool *)malloc(sizeof(JPool) * (npool + 1));
    d->poolel = (int *)malloc(sizeof(int) * (npe + 1));
    if (!d->tpl || !d->jig || !d->el || !d->listel || !d->pool || !d->poolel) return NULL;
    for (int i = 0; i < ntpl; i++) {
        const int32_t *t = tpl + 5 * i;
        d->tpl[i] = (JTpl){t[0], t[1], t[2], t[3], t[4]};
    }
    for (int i = 0; i < njig; i++) {
        const int32_t *t = jig + 11 * i;
        d->jig[i] = (JTJig){t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], t[8], t[9], t[10]};
    }
    for (int i = 0; i < nelem; i++) {
        const int32_t *t = el + 5 * i;
        d->el[i] = (JElem){t[0], t[1], t[2], t[3], t[4]};
    }
    memcpy(d->listel, listel, sizeof(int) * nlist);
    for (int i = 0; i < npool; i++) {
        const int32_t *t = pool + 5 * i;
        d->pool[i] = (JPool){t[0], t[1], t[2], t[3], t[4]};
    }
    memcpy(d->poolel, poolel, sizeof(int) * npe);
    d->feat_name = feat_name; d->feat_target = feat_target; d->feat_pool = feat_pool;
    return d;
}

/* ── geometry ─────────────────────────────────────────────────────────────── */
static inline void rot_pos(int x, int y, int z, int rot, int *o)
{
    switch (rot) {
    case 1: o[0] = -z; o[1] = y; o[2] = x; break;
    case 2: o[0] = -x; o[1] = y; o[2] = -z; break;
    case 3: o[0] = z; o[1] = y; o[2] = -x; break;
    default: o[0] = x; o[1] = y; o[2] = z;
    }
}
static inline int rot_dir(int d, int rot) { return d >= 4 ? d : (d + rot) & 3; }
static inline int imin(int a, int b) { return a < b ? a : b; }
static inline int imax(int a, int b) { return a > b ? a : b; }

/* 1: a box, 0: none (empty) */
static int ebox(const JData *d, int e, const int *pos, int rot, int *b)
{
    const JElem *E = &d->el[e];
    if (E->kind == 0) {
        const JTpl *T = &d->tpl[E->tpl];
        int a[3], c[3];
        rot_pos(0, 0, 0, rot, a);
        rot_pos(T->sx - 1, T->sy - 1, T->sz - 1, rot, c);
        for (int k = 0; k < 3; k++) { b[k] = imin(a[k], c[k]) + pos[k]; b[k + 3] = imax(a[k], c[k]) + pos[k]; }
        return 1;
    }
    if (E->kind == 1) { for (int k = 0; k < 3; k++) b[k] = b[k + 3] = pos[k]; return 1; }
    if (E->kind == 3) {
        int any = 0, t[6];
        for (int i = 0; i < E->nc; i++) {
            int c = d->listel[E->c0 + i];
            if (d->el[c].kind == 2) continue;
            if (!ebox(d, c, pos, rot, t)) continue;
            if (!any) { memcpy(b, t, sizeof t); any = 1; }
            else for (int k = 0; k < 3; k++) { b[k] = imin(b[k], t[k]); b[k + 3] = imax(b[k + 3], t[k + 3]); }
        }
        return any;
    }
    return 0;
}

typedef struct { int pos[3], front, top, name, target, pool, joint, pp, sp; } WJig;

static void shuffle_jigs(WJig *a, int n, uint64_t *r)
{
    for (int i = n; i > 1; i--) {
        int k = nextInt(r, i);
        WJig t = a[i - 1]; a[i - 1] = a[k]; a[k] = t;
    }
}

/* getShuffledJigsawBlocks: (n) into out (cap) */
static int ejigs(const JData *d, int e, const int *pos, int rot, uint64_t *r, WJig *out, int cap)
{
    const JElem *E = &d->el[e];
    if (E->kind == 3) return E->nc > 0 ? ejigs(d, d->listel[E->c0], pos, rot, r, out, cap) : 0;
    if (E->kind == 1) {
        if (cap < 1) return 0;
        WJig *j = &out[0];
        j->pos[0] = pos[0]; j->pos[1] = pos[1]; j->pos[2] = pos[2];
        j->front = 5; j->top = 2;
        j->name = d->feat_name; j->target = d->feat_target; j->pool = d->feat_pool;
        j->joint = 1; j->pp = 0; j->sp = 0;
        return 1;
    }
    if (E->kind == 2) return 0;
    const JTpl *T = &d->tpl[E->tpl];
    int n = 0;
    for (int i = 0; i < T->nj && n < cap; i++) {
        const JTJig *t = &d->jig[T->j0 + i];
        WJig *j = &out[n++];
        int o[3];
        rot_pos(t->x, t->y, t->z, rot, o);
        j->pos[0] = o[0] + pos[0]; j->pos[1] = o[1] + pos[1]; j->pos[2] = o[2] + pos[2];
        j->front = rot_dir(t->front, rot); j->top = rot_dir(t->top, rot);
        j->name = t->name; j->target = t->target; j->pool = t->pool;
        j->joint = t->joint; j->pp = t->pp; j->sp = t->sp;
    }
    shuffle_jigs(out, n, r);
    /* stable sort by -selection priority (insertion sort: stable) */
    for (int i = 1; i < n; i++) {
        WJig v = out[i];
        int k = i - 1;
        while (k >= 0 && out[k].sp < v.sp) { out[k + 1] = out[k]; k--; }
        out[k + 1] = v;
    }
    return n;
}

static inline int can_attach(const WJig *s, const WJig *t)
{
    return s->front == OPP[t->front] && (s->joint == 1 || s->top == t->top) &&
           (t->name < 0 || s->target == t->name);
}

/* ── free space: outer box [x0, x1+1) … minus holes ───────────────────────── */
typedef struct { int outer[6]; int *holes; int nh, ch; } JFree;

static int free_fits(const JFree *f, const int *b)
{
    const int *o = f->outer;
    if (!(b[0] >= o[0] && b[3] + 1 <= o[3] && b[1] >= o[1] && b[4] + 1 <= o[4] &&
          b[2] >= o[2] && b[5] + 1 <= o[5])) return 0;
    for (int i = 0; i < f->nh; i++) {
        const int *h = f->holes + 6 * i;
        if (b[0] <= h[3] && h[0] <= b[3] && b[1] <= h[4] && h[1] <= b[4] && b[2] <= h[5] && h[2] <= b[5])
            return 0;
    }
    return 1;
}

static int free_add(JFree *f, const int *b)
{
    if (f->nh == f->ch) {
        int nc = f->ch ? f->ch * 2 : 16;
        int *nh = (int *)realloc(f->holes, sizeof(int) * 6 * nc);
        if (!nh) return 0;
        f->holes = nh; f->ch = nc;
    }
    memcpy(f->holes + 6 * f->nh++, b, 6 * sizeof(int));
    return 1;
}

/* ── shuffles of the element lists ───────────────────────────────────────── */
static void shuffle_ints(int *a, int n, uint64_t *r)        /* Collections.shuffle */
{
    for (int i = n; i > 1; i--) {
        int k = nextInt(r, i);
        int t = a[i - 1]; a[i - 1] = a[k]; a[k] = t;
    }
}
static void shuffle_fastutil(int *a, int n, uint64_t *r)    /* ObjectArrays.shuffle */
{
    for (int i = n - 1; i >= 0; i--) {
        int k = nextInt(r, i + 1);
        int t = a[i]; a[i] = a[k]; a[k] = t;
    }
}

/* ── heights ──────────────────────────────────────────────────────────────── */
typedef struct { int mode, mc; uint64_t seed; int64_t *keys; int *vals; int cap; } JHeight;

static int height_at(JHeight *h, int x, int z)
{
    if (h->mode == 0) return 0;
    int64_t key = ((int64_t)x << 32) ^ (uint32_t)z;
    uint32_t i = (uint32_t)((uint64_t)key * 0x9E3779B97F4A7C15ULL >> 40) & (h->cap - 1);
    for (;;) {
        if (h->keys[i] == INT64_MIN) break;
        if (h->keys[i] == key) return h->vals[i];
        i = (i + 1) & (h->cap - 1);
    }
    int v;
    if (h->mode == 1) v = ssa_tf_surface(h->mc, h->seed, x, z) + 1;
    else v = ssa_t116_base_height(h->mc, h->seed, x, z);
    h->keys[i] = key; h->vals[i] = v;
    return v;
}

/* ── the placement ────────────────────────────────────────────────────────── */
typedef struct {
    int el, pos[3], rot, box[6], gld;
    int jun0, njun;                  /* filled at the end */
} JPiece;
typedef struct { int piece, x, y, z, dy, rigid; } JJunc;
typedef struct { int piece, freeid, depth, pp; int64_t order; } JQ;

typedef struct {
    const JData *d;
    uint64_t *r;
    JHeight *h;
    JPiece *pc; int npc, cpc;
    JJunc *jn; int njn, cjn;
    JFree *fr; int nfr, cfr;
    JQ *q; int nq, cq; int64_t qord;
    int depth_max, hack, legacy;
    int err;
} JCtx;

static int new_free(JCtx *c, const int *outer)
{
    if (c->nfr == c->cfr) {
        int nc = c->cfr ? c->cfr * 2 : 64;
        JFree *n = (JFree *)realloc(c->fr, sizeof(JFree) * nc);
        if (!n) { c->err = 1; return -1; }
        c->fr = n; c->cfr = nc;
    }
    JFree *f = &c->fr[c->nfr];
    memcpy(f->outer, outer, sizeof f->outer);
    f->holes = NULL; f->nh = f->ch = 0;
    return c->nfr++;
}
static int add_piece(JCtx *c, int el, const int *pos, int rot, const int *box, int gld)
{
    if (c->npc == c->cpc) {
        int nc = c->cpc ? c->cpc * 2 : 128;
        JPiece *n = (JPiece *)realloc(c->pc, sizeof(JPiece) * nc);
        if (!n) { c->err = 1; return -1; }
        c->pc = n; c->cpc = nc;
    }
    JPiece *p = &c->pc[c->npc];
    p->el = el; memcpy(p->pos, pos, sizeof p->pos); p->rot = rot;
    memcpy(p->box, box, sizeof p->box); p->gld = gld; p->jun0 = 0; p->njun = 0;
    return c->npc++;
}
static void add_junc(JCtx *c, int piece, int x, int y, int z, int dy, int rigid)
{
    if (c->njn == c->cjn) {
        int nc = c->cjn ? c->cjn * 2 : 256;
        JJunc *n = (JJunc *)realloc(c->jn, sizeof(JJunc) * nc);
        if (!n) { c->err = 1; return; }
        c->jn = n; c->cjn = nc;
    }
    c->jn[c->njn++] = (JJunc){piece, x, y, z, dy, rigid};
}
static void push_q(JCtx *c, int piece, int freeid, int depth, int pp)
{
    if (c->nq == c->cq) {
        int nc = c->cq ? c->cq * 2 : 128;
        JQ *n = (JQ *)realloc(c->q, sizeof(JQ) * nc);
        if (!n) { c->err = 1; return; }
        c->q = n; c->cq = nc;
    }
    c->q[c->nq++] = (JQ){piece, freeid, depth, pp, c->qord++};
}

#define JIG_CAP 256

static void place_children(JCtx *c, int src, int ctx_free, int depth)
{
    const JData *d = c->d;
    int sel = c->pc[src].el;
    int srigid = d->el[sel].rigid;
    int sbb[6]; memcpy(sbb, c->pc[src].box, sizeof sbb);
    int sbox_y = sbb[1];
    int src_free = -1;
    WJig sj_all[JIG_CAP];
    int nsj = ejigs(d, sel, c->pc[src].pos, c->pc[src].rot, c->r, sj_all, JIG_CAP);
    int *targets = NULL; int ctg = 0;
    for (int si = 0; si < nsj && !c->err; si++) {
        WJig sj = sj_all[si];
        int tpos[3] = {sj.pos[0] + DX[sj.front], sj.pos[1] + DY[sj.front], sj.pos[2] + DZ[sj.front]};
        int s_local_y = sj.pos[1] - sbox_y;
        int base_h = INT32_MIN;
        if (sj.pool < 0 || !d->pool[sj.pool].valid) continue;
        const JPool *tp = &d->pool[sj.pool];
        if (tp->fallback < 0 || !d->pool[tp->fallback].valid) continue;
        const JPool *fb = &d->pool[tp->fallback];
        int inside = sbb[0] <= tpos[0] && tpos[0] <= sbb[3] && sbb[1] <= tpos[1] && tpos[1] <= sbb[4] &&
                     sbb[2] <= tpos[2] && tpos[2] <= sbb[5];
        int cfree;
        if (inside) {
            if (src_free < 0) {
                int o[6] = {sbb[0], sbb[1], sbb[2], sbb[3] + 1, sbb[4] + 1, sbb[5] + 1};
                src_free = new_free(c, o);
                if (src_free < 0) break;
            }
            cfree = src_free;
        } else cfree = ctx_free;
        int nt = (depth != c->depth_max ? tp->ne : 0) + fb->ne;
        if (nt > ctg) { free(targets); ctg = nt + 16; targets = (int *)malloc(sizeof(int) * ctg); if (!targets) { c->err = 1; break; } }
        int k = 0;
        if (depth != c->depth_max) {
            memcpy(targets, d->poolel + tp->e0, sizeof(int) * tp->ne);
            if (c->legacy) shuffle_fastutil(targets, tp->ne, c->r); else shuffle_ints(targets, tp->ne, c->r);
            k = tp->ne;
        }
        memcpy(targets + k, d->poolel + fb->e0, sizeof(int) * fb->ne);
        if (c->legacy) shuffle_fastutil(targets + k, fb->ne, c->r); else shuffle_ints(targets + k, fb->ne, c->r);
        int placed = 0;
        for (int ti = 0; ti < nt && !placed; ti++) {
            int tel = targets[ti];
            if (d->el[tel].kind == 2) break;
            int rots[4] = {0, 1, 2, 3};
            shuffle_ints(rots, 4, c->r);
            for (int ri = 0; ri < 4 && !placed; ri++) {
                int trot = rots[ri];
                int zero[3] = {0, 0, 0};
                WJig tj_all[JIG_CAP];
                int ntj = ejigs(d, tel, zero, trot, c->r, tj_all, JIG_CAP);
                int hb[6];
                int hok = ebox(d, tel, zero, trot, hb);
                int expand = 0;
                if (c->hack && hok && hb[4] - hb[1] + 1 <= 16) {
                    for (int q = 0; q < ntj; q++) {
                        const WJig *tj = &tj_all[q];
                        int qq[3] = {tj->pos[0] + DX[tj->front], tj->pos[1] + DY[tj->front], tj->pos[2] + DZ[tj->front]};
                        if (!(hb[0] <= qq[0] && qq[0] <= hb[3] && hb[1] <= qq[1] && qq[1] <= hb[4] &&
                              hb[2] <= qq[2] && qq[2] <= hb[5])) continue;
                        int a = 0, b = 0;
                        if (tj->pool >= 0) {
                            a = d->pool[tj->pool].maxsize;
                            int fbp = d->pool[tj->pool].fallback;
                            b = fbp >= 0 ? d->pool[fbp].maxsize : 0;
                        }
                        expand = imax(expand, imax(a, b));
                    }
                }
                for (int q = 0; q < ntj && !placed; q++) {
                    const WJig *tj = &tj_all[q];
                    if (!can_attach(&sj, tj)) continue;
                    int raw[3] = {tpos[0] - tj->pos[0], tpos[1] - tj->pos[1], tpos[2] - tj->pos[2]};
                    int rbb[6];
                    if (!ebox(d, tel, raw, trot, rbb)) continue;
                    int trigid = d->el[tel].rigid;
                    int t_local_y = tj->pos[1];
                    int dY = s_local_y - t_local_y + DY[sj.front];
                    int tby;
                    if (srigid && trigid) tby = sbox_y + dY;
                    else {
                        if (base_h == INT32_MIN) base_h = height_at(c->h, sj.pos[0], sj.pos[2]);
                        tby = base_h - t_local_y;
                    }
                    int yo = tby - rbb[1];
                    int tbb[6] = {rbb[0], rbb[1] + yo, rbb[2], rbb[3], rbb[4] + yo, rbb[5]};
                    int tposn[3] = {raw[0], raw[1] + yo, raw[2]};
                    if (expand > 0) {
                        int ns = imax(expand + 1, tbb[4] - tbb[1]);
                        if (tbb[1] + ns > tbb[4]) tbb[4] = tbb[1] + ns;
                    }
                    if (!free_fits(&c->fr[cfree], tbb)) continue;
                    if (!free_add(&c->fr[cfree], tbb)) { c->err = 1; break; }
                    int sgld = c->pc[src].gld;
                    int tgld = trigid ? sgld - dY : 1;
                    int np = add_piece(c, tel, tposn, trot, tbb, tgld);
                    if (np < 0) break;
                    int jy;
                    if (srigid) jy = sbox_y + s_local_y;
                    else if (trigid) jy = tby + t_local_y;
                    else {
                        if (base_h == INT32_MIN) base_h = height_at(c->h, sj.pos[0], sj.pos[2]);
                        /* Python int(dY / 2): toward zero */
                        jy = base_h + dY / 2;
                    }
                    add_junc(c, src, tpos[0], jy - s_local_y + sgld, tpos[2], dY, trigid);
                    add_junc(c, np, sj.pos[0], jy - t_local_y + tgld, sj.pos[2], -dY, srigid);
                    if (depth + 1 <= c->depth_max) push_q(c, np, cfree, depth + 1, sj.pp);
                    placed = 1;
                }
            }
        }
    }
    free(targets);
}

static inline int jdiv2(int a) { return a / 2; }     /* C: toward zero, as Java */

/* out: per piece 13 ints (el, pos xyz, rot, box 6, gld, njunc), then the junctions
 * (x, y, z, dy, rigid) of every piece in piece order; centre[3]. Returns the number
 * of pieces, 0 none (empty start / no named jigsaw), -1 error / too small. */
JEXPORT int ssa_jig_generate(void *dp, int mc, uint64_t seed, int cx, int cz,
                             const int32_t *starts, int nstarts, int start_y, int size,
                             int mh, int mv, int start_name, int hack, int legacy, int hmode,
                             int32_t *out, int cap_pieces, int32_t *jout, int cap_junc,
                             int32_t *centre)
{
    const JData *d = (const JData *)dp;
    if (!d) return -1;
    uint64_t r = chunkGenerateRnd(seed, cx, cz);
    JHeight H = {hmode, mc, seed, NULL, NULL, 4096};
    if (hmode) {
        H.keys = (int64_t *)malloc(sizeof(int64_t) * H.cap);
        H.vals = (int *)malloc(sizeof(int) * H.cap);
        if (!H.keys || !H.vals) { free(H.keys); free(H.vals); return -1; }
        for (int i = 0; i < H.cap; i++) H.keys[i] = INT64_MIN;
    }
    int ret = 0;
    JCtx C;
    memset(&C, 0, sizeof C);
    C.d = d; C.r = &r; C.h = &H; C.depth_max = size; C.hack = hack; C.legacy = legacy;
    int sp = starts[0];
    if (nstarts > 1) sp = starts[nextInt(&r, nstarts)];     /* 1.16.1 bastions */
    int crot = nextInt(&r, 4);
    if (sp < 0 || d->pool[sp].ne <= 0) goto done;
    int cel = d->poolel[d->pool[sp].e0 + nextInt(&r, d->pool[sp].ne)];
    if (d->el[cel].kind == 2) goto done;
    int pos[3] = {cx << 4, start_y, cz << 4};
    int vy = 0;
    if (start_name >= 0) {
        WJig js[JIG_CAP];
        int n = ejigs(d, cel, pos, crot, &r, js, JIG_CAP), found = -1;
        for (int i = 0; i < n; i++) if (js[i].name == start_name) { found = i; break; }
        if (found < 0) goto done;
        int vx = js[found].pos[0] - pos[0], vz = js[found].pos[2] - pos[2];
        vy = js[found].pos[1] - pos[1];
        pos[0] -= vx; pos[1] -= vy; pos[2] -= vz;
    }
    int box[6];
    if (!ebox(d, cel, pos, crot, box)) goto done;
    int centerX = jdiv2(box[3] + box[0]), centerZ = jdiv2(box[5] + box[2]);
    int bottomY = start_name >= 0 ? pos[1] : pos[1] + height_at(&H, centerX, centerZ);
    int dy = bottomY - (box[1] + 1);
    box[1] += dy; box[4] += dy;
    int ppos[3] = {pos[0], pos[1] + dy, pos[2]};
    add_piece(&C, cel, ppos, crot, box, 1);
    int centerY = bottomY + vy;
    if (centre) { centre[0] = centerX; centre[1] = centerY; centre[2] = centerZ; }
    if (size > 0) {
        int outer[6] = {centerX - mh, imax(centerY - mv, J_MIN_Y), centerZ - mh,
                        centerX + mh + 1, imin(centerY + mv + 1, J_MAX_Y + 1), centerZ + mh + 1};
        int f0 = new_free(&C, outer);
        if (f0 >= 0) {
            free_add(&C.fr[f0], box);
            place_children(&C, 0, f0, 0);
            while (C.nq > 0 && !C.err) {
                int best = 0;
                for (int i = 1; i < C.nq; i++)
                    if (C.q[i].pp > C.q[best].pp ||
                        (C.q[i].pp == C.q[best].pp && C.q[i].order < C.q[best].order)) best = i;
                JQ e = C.q[best];
                memmove(C.q + best, C.q + best + 1, sizeof(JQ) * (C.nq - best - 1));
                C.nq--;
                place_children(&C, e.piece, e.freeid, e.depth);
            }
        }
    }
    if (C.err || C.npc > cap_pieces) { ret = -1; goto done; }
    /* junctions grouped per piece, in their order */
    {
        int nj = 0;
        for (int p = 0; p < C.npc; p++) {
            int cnt = 0;
            for (int i = 0; i < C.njn; i++) {
                if (C.jn[i].piece != p) continue;
                if (nj >= cap_junc) { ret = -1; goto done; }
                int32_t *o = jout + 5 * nj++;
                o[0] = C.jn[i].x; o[1] = C.jn[i].y; o[2] = C.jn[i].z; o[3] = C.jn[i].dy; o[4] = C.jn[i].rigid;
                cnt++;
            }
            C.pc[p].njun = cnt;
        }
        for (int p = 0; p < C.npc; p++) {
            int32_t *o = out + 13 * p;
            const JPiece *P = &C.pc[p];
            o[0] = P->el; o[1] = P->pos[0]; o[2] = P->pos[1]; o[3] = P->pos[2]; o[4] = P->rot;
            for (int k = 0; k < 6; k++) o[5 + k] = P->box[k];
            o[11] = P->gld; o[12] = P->njun;
        }
        ret = C.npc;
    }
done:
    for (int i = 0; i < C.nfr; i++) free(C.fr[i].holes);
    free(C.fr); free(C.pc); free(C.jn); free(C.q);
    free(H.keys); free(H.vals);
    return ret;
}
