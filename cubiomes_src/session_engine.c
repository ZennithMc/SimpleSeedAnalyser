/*
 * session_engine.c — Native C multithreaded seed evaluator for SSA Custom Sessions.
 *
 * Faithful port of Cubiomes Viewer's searchthread.cpp + search.cpp condition
 * evaluator logic, stripped of Qt/Lua dependencies. Compiles into libcubiomes.dll.
 *
 * Threading model (mirrors searchthread.cpp's SearchMaster/SearchWorker):
 *   - Python passes a flat SsaCond[] and a seed range.
 *   - Engine spawns N OS threads; each holds its own Generator + SurfaceNoise.
 *   - Work is doled out in adaptive chunks (item_size grows/shrinks based on
 *     wall-clock per chunk, identical idea to searchthread.cpp requestItem()).
 *   - Match callback is delivered under result_mutex so Python (with GIL)
 *     can safely append to its queue.
 *
 * V4 fixes vs V3:
 *   - Use getStructureConfig().regionSize per structure (was hardcoded 512).
 *   - Stronghold: full ring prefilter from search.cpp (~10x speedup on miss).
 *   - Per-condition cached BiomeFilter (one alloc per session, not per seed).
 *   - End City: isViableEndCityTerrain() check.
 *   - 1.18+: isViableStructureTerrain() check.
 *   - Village abandoned variant filter via getVariant().
 *   - Igloo basement variant filter via getVariant().
 *   - Time-based adaptive item sizing (matches searchthread.cpp formula).
 *   - Dimension switching cached (avoid redundant applySeed).
 *   - Per-thread CondCache to avoid recomputing setupBiomeFilter / getStructureConfig.
 */

#ifdef _WIN32
#  define WIN32_LEAN_AND_MEAN
#  include <windows.h>
#  include <process.h>
#  define THREAD_T   HANDLE
#  define MUTEX_T    CRITICAL_SECTION
#  define MUTEX_INIT(m)    InitializeCriticalSection(&(m))
#  define MUTEX_LOCK(m)    EnterCriticalSection(&(m))
#  define MUTEX_UNLOCK(m)  LeaveCriticalSection(&(m))
#  define MUTEX_DESTROY(m) DeleteCriticalSection(&(m))
#  define EXPORT __declspec(dllexport)
#else
#  include <pthread.h>
#  include <time.h>
#  define THREAD_T   pthread_t
#  define MUTEX_T    pthread_mutex_t
#  define MUTEX_INIT(m)    pthread_mutex_init(&(m), NULL)
#  define MUTEX_LOCK(m)    pthread_mutex_lock(&(m))
#  define MUTEX_UNLOCK(m)  pthread_mutex_unlock(&(m))
#  define MUTEX_DESTROY(m) pthread_mutex_destroy(&(m))
#  define EXPORT __attribute__((visibility("default")))
#endif

#include "finders.h"
#include "generator.h"
#include "biomenoise.h"
#include "noise.h"
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <float.h>
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
#  ifdef _MSC_VER
#    include <intrin.h>
#  else
#    include <x86intrin.h>
#  endif
#  define SSA_TICKS() ((uint64_t)__rdtsc())
#else
#  define SSA_TICKS() ((uint64_t)0)
#endif
#ifdef _OPENMP
#include <omp.h>
#endif

/* ═══════════════════════════════════════════════════════════════════════════
 * Constants — must match Python custom_search.py
 * ═══════════════════════════════════════════════════════════════════════════ */
#define CTYPE_SPIRAL              0
#define CTYPE_SCALE_TO_NETHER     1
#define CTYPE_SCALE_TO_OVERWORLD  2
#define CTYPE_FIRST_STRONGHOLD    3
#define CTYPE_STRONGHOLD          4
#define CTYPE_STRUCTURE_OW        5
#define CTYPE_STRUCTURE_NETHER    6
#define CTYPE_STRUCTURE_END       7
#define CTYPE_MINESHAFT           8
#define CTYPE_SLIME               9
#define CTYPE_SPAWN               10
#define CTYPE_BIOME_FILTER        11
#define CTYPE_BIOME_SAMPLE        12
#define CTYPE_BIOME_CENTER        13
#define CTYPE_CLIMATE_MINMAX      14
#define CTYPE_CLIMATE_NOISE       15
#define CTYPE_HEIGHT              16
#define CTYPE_LOGIC_OR            17
#define CTYPE_LOGIC_NOT           18
#define CTYPE_CRUNCHINESS         19
#define CTYPE_NOISE_SAMPLE        20
#define CTYPE_CLIMATE_AREA        21   /* Locate climate center: coarse flood fill (01/10) */

#define VARIANT_NONE          0
#define VARIANT_ABANDONED     1
#define VARIANT_NOT_ABANDONED 2
#define VARIANT_BASEMENT      3
#define VARIANT_NO_BASEMENT   4

#define SSA_MODE_INC    0
#define SSA_MODE_FAM48  1
#define SSA_MODE_BLOCKS 2

#define MAX_CONDS       128
#define MAX_BIOMES_LIST 64
#define NP_MAX_LOCAL    6
#define MAX_THREADS     128

/* (p92) MC_1_9 / MC_1_18 were re-#defined here as 9 / 21: those names are
 * enum members of biomes.h, so #ifndef was always true and « MC_1_18 » meant
 * 1.17 in this file. The cubiomes enum values are used now. */

/* ═══════════════════════════════════════════════════════════════════════════
 * SsaCond — must match the Python ctypes.Structure layout exactly
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef struct {
    int32_t  ctype;
    int32_t  cond_idx;
    int32_t  parent_idx;

    int32_t  x1, z1, x2, z2;
    int32_t  rmax;

    int32_t  min_count;
    int32_t  exclude;

    int32_t  struct_type;
    int32_t  variant_filter;
    int32_t  step;

    int32_t  biomes_incl[MAX_BIOMES_LIST];
    int32_t  n_incl;
    int32_t  biomes_excl[MAX_BIOMES_LIST];
    int32_t  n_excl;
    int32_t  biome_scale;
    int32_t  biome_dim;
    int32_t  biome_y;
    int32_t  biome_match_any;   /* 1 = at least one incl biome must be present
                                  * (cubiomes BF_MATCH_ANY semantics) */

    double   coverage;
    double   confidence;

    int32_t  biome_id;
    int32_t  biome_min_size;
    int32_t  biome_tolerance;

    int32_t  climate_para;
    int32_t  climate_mode;
    double   climate_vmin;
    double   climate_vmax;
    int32_t  climate_nmax;
    int32_t  climate_invert;

    int32_t  cn_ok_lo[NP_MAX_LOCAL];
    int32_t  cn_ok_hi[NP_MAX_LOCAL];
    int32_t  cn_ex_lo[NP_MAX_LOCAL];
    int32_t  cn_ex_hi[NP_MAX_LOCAL];

    float    height_ymin;
    float    height_ymax;
    int32_t  height_invert;

    /* Crunchiness fields (only used when ctype == CTYPE_CRUNCHINESS).
     * Octave mask: bit i of low 16 = include octave_a[i], bit i of high 16
     * = include octave_b[i]. Up to 6 octave pairs supported. Weights are
     * applied in pair order: weights[0]=octA[0], weights[1]=octB[0],
     * weights[2]=octA[1], ..., weights[10]=octA[5], weights[11]=octB[5]. */
    int32_t  crunch_climate;       /* 0=Temp, 1=Hum, 2=Cont, 3=Eros, 5=Weird */
    uint32_t crunch_octave_mask;
    float    crunch_weights[12];
    int32_t  crunch_mode;          /* 0=crunchy (score<=threshold), 1=bland */
    float    crunch_threshold;

    /* cubiomes-viewer "Ignore reference" (skipref): an instance located exactly
     * at the reference position (the parent structure itself) is not counted. */
    int32_t  skip_ref;

    /* biome / noise sample: cubiomes-viewer « Yield individual samples »
     * (count == 1) — dependent conditions are tested around EACH sampled
     * position (OR) instead of the centre of the samples. */
    int32_t  yield_samples;
} SsaCond;

/* ═══════════════════════════════════════════════════════════════════════════
 * Per-condition runtime cache (thread-local — one set per worker)
 * Avoids recomputing setupBiomeFilter() and getStructureConfig() per seed.
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef struct {
    BiomeFilter      bf;
    int              bf_ready;
    StructureConfig  sconf;
    int              sconf_ready;
    /* p92: exact climate pruning of the biome conditions (1.18+ overworld)
     * bp_state 0 = not set up, 1 = bp usable, -1 = no pruning (bp unused) */
    int              bp_state;
    BiomePrune       bp;
    int              bp_id[BPRUNE_MAX];
} CondCache;

/* ═══════════════════════════════════════════════════════════════════════════
 * Per-thread state
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef struct {
    int     valid;
    int32_t x, z;
} SsaPos;

struct ChildOrderCache_s;   /* defined below; one per search (see build_child_order) */

typedef struct {
    Generator    g;
    SurfaceNoise sn_ow;
    SurfaceNoise sn_end;
    int          sn_ow_ready;
    int          sn_end_ready;
    int          current_dim;
    int          mc;
    int          large;
    uint64_t     seed;
    SsaPos       positions[MAX_CONDS + 1];   /* condition indices are 1..MAX_CONDS */
    /* Per-climate noise cache, keyed by (para, nmax). Une session mélange
     * souvent plusieurs nmax sur le MÊME paramètre (ex: continentalness en
     * nmax=1, nmax=2 et full sur la même seed). Un cache à une seule entrée par
     * para se faisait invalider à chaque changement de nmax → recalcul permanent
     * de setClimateParaSeed (jusqu'à 18µs pour le full). On garde donc un slot
     * par nmax distinct : index 0 = nmax 1, 1 = nmax 2, 2 = full (-1), 3 = autre. */
    #define CLIM_NSLOT 4
    BiomeNoise   climate_cache[NP_MAX][CLIM_NSLOT];
    int          climate_cache_nmax[NP_MAX][CLIM_NSLOT]; /* -2 = invalide */
    uint64_t     climate_cache_seed[NP_MAX][CLIM_NSLOT];
    /* p93: climate octaves shared by all octave counts, built on demand
     * (before: one full re-seeding per (para, nmax) slot; nmax 4/6/8 shared
     * slot 3 and re-seeded each other at every switch) */
    ClimLazy          clazy[NP_MAX];
    DoublePerlinNoise cview[NP_MAX][CLIM_NSLOT];
    /* Cache pour le bruit climat chargé dans g.bn (utilisé par noise_sample
     * Monte-Carlo, qui lit g->bn.climate[para] via monteCarloBiomes). */
    uint64_t     gbn_seed;
    int          gbn_nmax;
    /* Pointeur vers le drapeau d'arrêt global (peut être NULL). Vérifié dans
     * les boucles d'évaluation longues (spiral) pour que Stop réponde même
     * pendant l'évaluation d'une seule seed coûteuse. */
    volatile int *stop_flag;
    /* evaluation order of the children of each condition, computed once for
     * THIS search and owned by it (before: one global cache keyed by the array
     * pointer — a new search whose array landed at the same address with the
     * same number of conditions reused the OLD tree → wrong results) */
    const struct ChildOrderCache_s *co;
    /* biome / noise sample « yield »: where the next sample evaluation writes
     * each accepted position (set by eval_node for ONE call, then cleared) */
    Pos         *sample_out;
    int          sample_cap;
    int          sample_n;
    /* per-condition profiler of the search (NULL outside the engine) */
    struct SsaProf_s *prof;
} SsaEnv;

/* ── Profiler (27/09) ─────────────────────────────────────────────────────
 * For every condition: how many seeds reached it, how many passed it (its
 * subtree, at least once for that seed) and the CPU time of its OWN work
 * (children excluded), sampled on 1 seed in 32 with the TSC. Counters are
 * per thread and merged into the engine state at the end of each chunk. */
typedef struct SsaProf_s {
    uint64_t stamp;                 /* seed counter of this thread */
    int      timing;                /* this seed is timed */
    uint64_t child_ticks;           /* accumulator for the running node */
    uint64_t reach_stamp[MAX_CONDS];
    uint64_t pass_stamp[MAX_CONDS];
    int64_t  reached[MAX_CONDS];
    int64_t  passed[MAX_CONDS];
    /* the condition's OWN test, per check (29/09): a relative condition is
     * checked once per parent position, and passed[] above counts the whole
     * branch — with no result every branch showed 0 %, the stats said
     * nothing about which condition blocks */
    int64_t  checks[MAX_CONDS];
    int64_t  checks_ok[MAX_CONDS];
    uint64_t ticks[MAX_CONDS];
    uint64_t total_ticks;           /* whole seed tests (timed seeds) */
} SsaProf;

static inline void prof_check(SsaEnv *env, int cond_1idx, int ok)
{
    SsaProf *pf = env->prof;
    int i = cond_1idx - 1;
    if (!pf || i < 0 || i >= MAX_CONDS) return;
    pf->checks[i]++;
    if (ok) pf->checks_ok[i]++;
}

static inline void prof_seed_begin(SsaProf *pf)
{
    if (!pf) return;
    pf->stamp++;
    pf->timing = (pf->stamp & 31) == 0;
    pf->child_ticks = 0;
}

/* Mappe un nmax vers un slot de cache stable. */
static inline int clim_slot_for_nmax(int nmax) {
    if (nmax == 1) return 0;
    if (nmax == 2) return 1;
    if (nmax <= 0) return 2;   /* full (-1) */
    return 3;                   /* autre valeur */
}

/* p93: the climate noise (para, nmax) of the current seed. Octaves come from
 * the shared on-demand cache (same noise, same values as setClimateParaSeed,
 * see climLazyGet); para NP_DEPTH keeps the old per-slot generator. The
 * pointer is valid until the next call for the same (para, slot). */
static const DoublePerlinNoise *clim_dp(SsaEnv *env, int para, int nmax)
{
    int slot = clim_slot_for_nmax(nmax);
    if (para != NP_DEPTH &&
        climLazyGet(&env->clazy[para], &env->cview[para][slot], env->seed,
                    env->large, para, nmax))
        return &env->cview[para][slot];
    BiomeNoise *bn = &env->climate_cache[para][slot];
    if (!(env->climate_cache_seed[para][slot] == env->seed &&
          env->climate_cache_nmax[para][slot] == nmax)) {
        setClimateParaSeed(bn, env->seed, env->large, para, nmax);
        env->climate_cache_seed[para][slot] = env->seed;
        env->climate_cache_nmax[para][slot] = nmax;
    }
    return &bn->climate[para];
}

/* Forward declarations */
static int cond_cost_heuristic(const SsaCond *c);
static int64_t spiral_positions(const SsaCond *c);
#define COST_CAP ((int64_t)1 << 40)
/* Coût d'un sous-arbre = max(coût du nœud, coûts des descendants). Permet au
 * tri des enfants de tenir compte d'une condition très chère (ex. spawn 3ms)
 * cachée en profondeur sous des spirales bon marché : la branche qui mène au
 * spawn hérite ainsi du coût du spawn et passe APRÈS les conditions sélectives
 * (noise_sample, climat). C'est la clé pour ne presque jamais atteindre le spawn. */
/* Costs are estimated MICROSECONDS per evaluation (see cond_cost_heuristic).
 * A spiral / iterator runs its children at EVERY position until one passes —
 * on a failing seed, at all of them, the cheapest child rejecting each one:
 * its cost is positions × the cheapest child. Before, a spiral counted as 6
 * whatever its size: a step-16 spiral over 3 500 blocks (48 000 positions,
 * ~0.3 s per seed) was ordered BEFORE a 20 µs noise sample that rejects 90 %
 * of the seeds — 250× slower than cubiomes-viewer on such sessions. */
static int64_t cond_subtree_cost(const SsaCond *conds, int n_conds, int cond_1idx) {
    /* Expected cost to REJECT a seed through this node (not the max of the
     * subtree): the children of a condition only run once it has passed, so
     * a 1 µs, very selective climate point with expensive children must still
     * go first (before: max(subtree) sent it last — 3x slower than
     * cubiomes-viewer on « fortest 1 », where that point rejects 97 %).
     *   leaf / condition : self + 1/4 of its cheapest child (it may pass)
     *   spiral           : positions × cheapest child (all positions on a fail)
     *   OR / NOT         : all children must be tried → their sum
     *   scale ×8 / ÷8    : pass-through → cheapest child */
    const SsaCond *me = NULL;
    for (int i = 0; i < n_conds; i++) {
        if (conds[i].cond_idx == cond_1idx) { me = &conds[i]; break; }
    }
    if (!me) return 0;
    int64_t self = cond_cost_heuristic(me);
    int64_t minchild = -1, sumchild = 0;
    int nkids = 0;
    for (int i = 0; i < n_conds; i++) {
        if (conds[i].cond_idx && conds[i].parent_idx == cond_1idx
            && conds[i].cond_idx != cond_1idx) {  /* anti-cycle */
            int64_t childc = cond_subtree_cost(conds, n_conds, conds[i].cond_idx);
            if (minchild < 0 || childc < minchild) minchild = childc;
            sumchild += childc;
            if (sumchild > COST_CAP) sumchild = COST_CAP;
            nkids++;
        }
    }
    int64_t cost;
    if (!nkids) {
        cost = self;
    } else if (me->ctype == CTYPE_SPIRAL) {
        cost = self + spiral_positions(me) * minchild;
    } else if (me->ctype == CTYPE_LOGIC_OR || me->ctype == CTYPE_LOGIC_NOT) {
        cost = self + sumchild;
    } else if (me->ctype == CTYPE_SCALE_TO_NETHER || me->ctype == CTYPE_SCALE_TO_OVERWORLD) {
        cost = self + minchild;
    } else {
        cost = self + minchild / 4;
    }
    if (cost > COST_CAP || cost < 0) cost = COST_CAP;
    return cost;
}

/* Cache précalculé de la liste triée des enfants de chaque condition.
 * L'arbre de conditions est constant pendant toute une recherche, donc l'ordre
 * d'évaluation (cheap-first) est identique pour toutes les graines : on le
 * calcule UNE fois par configuration au lieu de refaire un tri O(n²) récursif
 * à chaque eval_node de chaque graine. Indexé par cond_idx (1..n). */
#define KIDS_MAXN (MAX_CONDS + 1)
typedef struct ChildOrderCache_s {
    int    ready;
    int    sig_n;
    const SsaCond *sig_ptr;
    int    n_kids[KIDS_MAXN];
    int    kids[KIDS_MAXN][MAX_CONDS];
} ChildOrderCache;

static void build_child_order(ChildOrderCache *coc, const SsaCond *conds, int n_conds) {
    /* Idempotent: if already built for these exact conds, this is a no-op (read-only).
     * Lets multiple threads call ssa_test_seeds_batch concurrently after one warm-up
     * call (they only READ the order then) -> parallel survivor validation. */
    if (coc->ready && coc->sig_ptr == conds && coc->sig_n == n_conds) return;
    for (int p = 0; p < KIDS_MAXN; p++) coc->n_kids[p] = 0;
    /* collecte des enfants par parent (parent_idx peut valoir 0..n) */
    for (int i = 0; i < n_conds; i++) {
        if (!conds[i].cond_idx) continue;
        int par = conds[i].parent_idx;
        if (par < 0 || par >= KIDS_MAXN) continue;
        if (par == conds[i].cond_idx) continue;  /* anti-cycle : auto-parent */
        int *nk = &coc->n_kids[par];
        if (*nk < MAX_CONDS) coc->kids[par][(*nk)++] = conds[i].cond_idx;
    }
    /* tri cheap-first par coût de sous-arbre pour chaque parent */
    for (int p = 0; p < KIDS_MAXN; p++) {
        int nk = coc->n_kids[p];
        int *arr = coc->kids[p];
        for (int a = 1; a < nk; a++) {
            int key = arr[a];
            int64_t kc = cond_subtree_cost(conds, n_conds, key);
            int b = a - 1;
            while (b >= 0 && cond_subtree_cost(conds, n_conds, arr[b]) > kc) {
                arr[b+1] = arr[b]; b--;
            }
            arr[b+1] = key;
        }
    }
    coc->sig_n = n_conds;
    coc->sig_ptr = conds;
    coc->ready = 1;
}

/* Cache global (rempli une fois par recherche ; les threads partagent la même
 * config de conditions en lecture seule). */
static ChildOrderCache *child_order_new(const SsaCond *conds, int n_conds) {
    ChildOrderCache *co = (ChildOrderCache *)calloc(1, sizeof(ChildOrderCache));
    if (co) build_child_order(co, conds, n_conds);
    return co;
}
static int test_crunchiness(SsaEnv *env, const SsaCond *c);

static inline void env_reset_positions(SsaEnv *e) {
    memset(e->positions, 0, sizeof(e->positions));
    e->positions[0].valid = 1;
}
/* Reset limité aux n premières positions (cond_idx 0..n) : évite de memset les
 * 128 slots quand la session n'a que ~19 conditions. */
static inline void env_reset_positions_n(SsaEnv *e, int n) {
    if (n < 1) n = 1;
    if (n > MAX_CONDS) n = MAX_CONDS;
    memset(e->positions, 0, (size_t)(n + 1) * sizeof(SsaPos));
    e->positions[0].valid = 1;
}

static inline void env_set_dim(SsaEnv *e, int dim) {
    if (e->current_dim != dim) {
        applySeed(&e->g, dim, e->seed);
        e->current_dim = dim;
    }
}

static inline void env_push(SsaEnv *e, int idx, int32_t x, int32_t z) {
    if (idx >= 0 && idx <= MAX_CONDS) {
        e->positions[idx].valid = 1;
        e->positions[idx].x = x;
        e->positions[idx].z = z;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * isSlimeChunk — pure arithmetic (matches finders.h isSlimeChunk inline)
 * ═══════════════════════════════════════════════════════════════════════════ */
static inline int is_slime_chunk(uint64_t seed, int32_t cx, int32_t cz) {
    /* cubiomes' own isSlimeChunk. The local copy computed
     * (int32)(cz*cz) * 0x4307a7 in 32 bits (int32 × uint32) instead of 64:
     * every slime chunk beyond |z| ≈ 31 chunks (~500 blocks) was wrong. */
    return isSlimeChunk(seed, cx, cz);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Callbacks for getParaRange (climate)
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef struct { double vmin,vmax; int xmin,zmin,xmax,zmax; } TrackMM;
static int f_track_mm(void *d, int x, int z, double p) {
    TrackMM *t = (TrackMM*)d;
    if (p < t->vmin) { t->vmin=p; t->xmin=x; t->zmin=z; }
    if (p > t->vmax) { t->vmax=p; t->xmax=x; t->zmax=z; }
    return 0;
}
/* Variante avec early-exit : connaît les bornes de la condition et s'arrête
 * dès qu'une violation rend l'échec CERTAIN. Pour une condition "max <= hi",
 * un point > hi condamne la zone → stop immédiat (au lieu de balayer 1M de
 * points). C'est exact : on n'a pas besoin du min/max précis pour rejeter.
 * fail_lo / fail_hi sont les seuils dont le franchissement garantit l'échec
 * (mis à ±INFINITY quand le mode ne teste pas ce côté). */
typedef struct {
    double vmin, vmax; int xmin, zmin, xmax, zmax;
    double fail_below, fail_above;  /* p < fail_below OU p > fail_above ⇒ échec certain */
    int failed;
} TrackMMEarly;
static int f_track_mm_early(void *d, int x, int z, double p) {
    TrackMMEarly *t = (TrackMMEarly*)d;
    if (p < t->vmin) { t->vmin=p; t->xmin=x; t->zmin=z; }
    if (p > t->vmax) { t->vmax=p; t->xmax=x; t->zmax=z; }
    if (p < t->fail_below || p > t->fail_above) { t->failed = 1; return 1; }
    return 0;
}
typedef struct { double lo,hi; } Confine;
static int f_confine(void *d, int x, int z, double p) {
    (void)x;(void)z;
    Confine *c=(Confine*)d; return (p<c->lo||p>c->hi)?1:0;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * p92 — fast exact biome sampling for the 1.18+ overworld
 *
 * ssa_biome_at() = getBiomeAt(g, scale, x, y, z) (same coordinates and
 * sampling flags as genBiomeNoiseScaled for a 1×1 range) without its
 * malloc/free, and, given candidates, sampleBiomeNoisePruned(): the climate
 * parameters are computed cheapest first and the cell is dropped (-2) as soon
 * as they prove none of the candidate biomes can be there — exact bounds taken
 * from the climate tree itself (see getBiomeLeafLimits), not the approximate
 * getBiomeParaLimits table. Result identical to getBiomeAt, validated on
 * millions of cells for every version (tests/biome_prune_check.c).
 * Requires g->bn to be the full biome noise (nptype == -1).
 * ═══════════════════════════════════════════════════════════════════════════ */
static inline int ssa_biome_at(const Generator *g, const BiomePrune *bp,
                               uint32_t alive, int scale, int x, int y, int z,
                               uint32_t xf)
{
    if (scale == 1) {
        int x4, y4, z4;
        voronoiAccess3D(g->sha, x, y, z, &x4, &y4, &z4);
        x = x4; y = y4; z = z4; scale = 4;
    }
    if (scale == 4)
        return bp ? sampleBiomeNoisePruned(&g->bn, x, y, z, NULL, xf, bp, alive)
                  : sampleBiomeNoise(&g->bn, NULL, x, y, z, NULL, 0);
    int s = scale / 4, mid = s / 2;
    uint64_t dat = 0;
    x = x * s + mid; z = z * s + mid;
    return bp ? sampleBiomeNoisePruned(&g->bn, x, y, z, &dat, SAMPLE_NO_SHIFT | xf, bp, alive)
              : sampleBiomeNoise(&g->bn, NULL, x, y, z, &dat, SAMPLE_NO_SHIFT);
}

/* the candidate biomes of a condition: every biome whose presence can change
 * the answer (bits of the 64-bit id masks, as in checkForBiomes) */
static void cache_setup_prune(CondCache *cache, int mc, uint64_t b, uint64_t m)
{
    int n = 0, ok = 1;
    for (int id = 0; id < 256 && ok; id++) {
        int bit = id < 128 ? (int)((b >> (id & 63)) & 1) * (id < 64)
                           : (int)((m >> ((id - 128) & 63)) & 1) * (id < 192);
        if (!bit) continue;
        if (n >= BPRUNE_MAX) { ok = 0; break; }
        cache->bp_id[n++] = id;
    }
    if (ok && n > 0 && mc >= MC_1_18 && setupBiomePrune(&cache->bp, mc, cache->bp_id, n))
        cache->bp_state = 1;
    else
        cache->bp_state = -1;
}

/* checkForBiomes() for the 1.18+ overworld, exact same decision: in
 * cubiomes the answer depends only on the set of biomes of the area (it scans
 * everything unless a required biome set is complete with no exclusion, or an
 * excluded biome is seen). Here: coarse-to-fine scan (positives stop early),
 * cells that cannot hold a still-useful biome are skipped after T/H/…, 1:1
 * cells share their 1:4 Voronoi source cell. Returns -1 when not applicable
 * (caller falls back to checkForBiomes). */
/* p93: the coarse-to-fine scan visits cells far apart → scattered Perlin
 * (same values, ~2× faster here than the switch gradient) */
#define FCB_XF SAMPLE_SSA_SCATTERED
static int fast_check_biomes(SsaEnv *env, CondCache *cache, Range r, int dim)
{
    Generator *g = &env->g;
    const BiomeFilter *bf = &cache->bf;
    if (env->mc < MC_1_18 || dim != DIM_OVERWORLD || (bf->flags & BF_APPROX))
        return -1;
    if (r.scale != 1 && r.scale != 4 && r.scale != 16 && r.scale != 64 && r.scale != 256)
        return -1;
    if (g->dim != dim || g->seed != env->seed)
        applySeed(g, dim, env->seed);
    if (g->bn.nptype != -1)
        return -1;
    volatile int *stop = env->stop_flag;
    if (stop && *stop) return 0;

    const uint64_t breq = bf->biomeToFind, mreq = bf->biomeToFindM;
    const uint64_t bexc = bf->biomeToExcl, mexc = bf->biomeToExclM;
    const uint64_t bany = bf->biomeToPick, many = bf->biomeToPickM;
    if (!cache->bp_state)
        cache_setup_prune(cache, env->mc, breq | bexc | bany, mreq | mexc | many);
    const BiomePrune *bp = cache->bp_state > 0 ? &cache->bp : NULL;
    const int has_exc = (bexc | mexc) != 0;
    uint64_t b = 0, m = 0;
    uint32_t alive = 0;

#define FCB_DONE_NEG()  (((b & bexc) | (m & mexc)) != 0)
#define FCB_REQ_OK()    ((b & breq) == breq && (m & mreq) == mreq)
#define FCB_ANY_OK()    ((bany | many) == 0 || (b & bany) || (m & many))
#define FCB_ALIVE() do { alive = 0; if (bp) for (int q = 0; q < bp->n; q++) { \
        int id_ = cache->bp_id[q]; \
        uint64_t bb_ = id_ < 128 ? b : m, mk_ = 1ULL << (id_ & 63); \
        uint64_t ex_ = id_ < 128 ? bexc : mexc, rq_ = id_ < 128 ? breq : mreq, an_ = id_ < 128 ? bany : many; \
        if ((ex_ & mk_) || ((rq_ & mk_) && !(bb_ & mk_)) || ((an_ & mk_) && !FCB_ANY_OK())) \
            alive |= 1u << q; } } while (0)

    if (!has_exc && FCB_REQ_OK() && FCB_ANY_OK())
        return 1;                          /* nothing to look for */
    FCB_ALIVE();

    int *memo = NULL;
    Range s = r;
    if (r.scale == 1) {
        s = getVoronoiSrcRange(r);
        size_t ns = (size_t)s.sx * s.sz * (s.sy > 0 ? s.sy : 1);
        memo = (int*) malloc(ns * sizeof(int));
        if (!memo) return -1;
        memset(memo, 0xff, ns * sizeof(int));          /* -1 = not sampled */
    }
    int top = 1;
    while (top < 16 && top * 2 <= (r.sx > r.sz ? r.sx : r.sz)) top *= 2;
    int ret = -2, polls = 0;
    for (int st = top; st >= 1 && ret == -2; st >>= 1) {
        for (int j = 0; j < r.sz && ret == -2; j += st) {
            if (stop && (++polls & 63) == 0 && *stop) { ret = 0; break; }
            for (int i = 0; i < r.sx; i += st) {
                if (st < top && (i % (2*st)) == 0 && (j % (2*st)) == 0)
                    continue;                          /* seen at a coarser step */
                int id;
                if (memo) {
                    int x4, y4, z4;
                    voronoiAccess3D(g->sha, r.x + i, r.y, r.z + j, &x4, &y4, &z4);
                    int k = ((y4 - s.y) * s.sz + (z4 - s.z)) * s.sx + (x4 - s.x);
                    id = memo[k];
                    if (id == -1)
                        id = memo[k] = bp ? sampleBiomeNoisePruned(&g->bn, x4, y4, z4, NULL, FCB_XF, bp, alive)
                                          : sampleBiomeNoise(&g->bn, NULL, x4, y4, z4, NULL, 0);
                } else {
                    id = ssa_biome_at(g, bp, alive, r.scale, r.x + i, r.y, r.z + j, FCB_XF);
                }
                if (id < 0) continue;
                uint64_t ob = b, om = m;
                if (id < 128) b |= 1ULL << id; else m |= 1ULL << (id - 128);
                if (b == ob && m == om) continue;
                if (FCB_DONE_NEG()) { ret = 0; break; }
                if (!has_exc && FCB_REQ_OK() && FCB_ANY_OK()) { ret = 1; break; }
                FCB_ALIVE();
            }
        }
    }
    free(memo);
    if (ret == -2)
        ret = (!FCB_DONE_NEG() && FCB_REQ_OK() && FCB_ANY_OK()) ? 1 : 0;
    if (stop && *stop) ret = 0;
#undef FCB_DONE_NEG
#undef FCB_REQ_OK
#undef FCB_ANY_OK
#undef FCB_ALIVE
    return ret;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Monte Carlo biome sampler callback
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef struct {
    const int32_t *incl; int n_incl;
    const int32_t *excl; int n_excl;
    int64_t xsum, zsum, n;
    int32_t at_x, at_z;
    int64_t rmax_sq;
    Pos *out; int *nout; int cap;   /* yield: accepted positions (nullable) */
    int fast;                       /* p92: ssa_biome_at (1.18+ overworld) */
    const BiomePrune *bp; uint32_t alive;
} MonteData;

static int monte_eval(Generator *g, int scale, int x, int y, int z, void *data) {
    MonteData *d = (MonteData*)data;
    /* IMPORTANT: when a sample falls outside the rmax disk, we MUST return -1
     * so that monteCarloBiomes() drops the sample from the statistics (matches
     * cubiomes-viewer's f_biome_sampler behavior). Returning 0 would count it
     * as a 'negative' sample and skew the coverage estimate, especially with
     * very low coverage thresholds (e.g. Cherry grove = 0.0001).
     */
    if (d->rmax_sq) {
        int64_t dx = (int64_t)(x*scale - d->at_x);
        int64_t dz = (int64_t)(z*scale - d->at_z);
        if (dx*dx + dz*dz >= d->rmax_sq) return -1;
    }
    /* Use y from the range as cubiomes-viewer does (was hardcoded 320 before
     * which is above the world ceiling and gave garbage biome IDs). */
    int id;
    if (d->fast) {
        id = ssa_biome_at(g, d->bp, d->alive, scale, x, y, z, SAMPLE_SSA_SCATTERED);
        if (id == -2) return 0;            /* neither included nor excluded */
    } else {
        id = getBiomeAt(g, scale, x, y, z);
    }
    for (int i = 0; i < d->n_excl; i++) if (d->excl[i] == id) return 0;
    for (int i = 0; i < d->n_incl; i++) {
        if (d->incl[i] == id) {
            d->xsum += (int64_t)x * scale;
            d->zsum += (int64_t)z * scale;
            if (d->out && *d->nout < d->cap) {
                d->out[*d->nout].x = x * scale; d->out[*d->nout].z = z * scale; (*d->nout)++;
            }
            d->n++;
            return 1;
        }
    }
    return 0;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Monte Carlo NOISE sampler — réplique exacte de f_noise_sampler de
 * cubiomes-viewer (search.cpp). Échantillonne un paramètre climatique (Perlin)
 * et teste vmin < v*10000 < vmax, avec inversion optionnelle. monteCarloBiomes
 * exige ensuite qu'une fraction `coverage` des points satisfasse (confiance
 * `confidence`). C'est la VRAIE sémantique de F_NOISE_SAMPLE — bien plus
 * sélective que le simple "∃ un point" que SSA utilisait avant.
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef struct {
    const DoublePerlinNoise *dp;   /* climate noise for (seed, para, nmax) */
    int      para;          /* NP_* climat */
    double   vmin, vmax;    /* bornes en unités x10000 */
    int      invert;
    int64_t  xsum, zsum; int n;
    int32_t  at_x, at_z;
    int64_t  rmax_sq;
    Pos *out; int *nout; int cap;   /* yield: accepted positions (nullable) */
} MonteNoiseData;

static int monte_noise_eval(Generator *g, int scale, int x, int y, int z, void *data) {
    (void)y;
    MonteNoiseData *d = (MonteNoiseData*)data;
    if (d->rmax_sq) {
        int64_t dx = (int64_t)(x*scale - d->at_x);
        int64_t dz = (int64_t)(z*scale - d->at_z);
        if (dx*dx + dz*dz >= d->rmax_sq) return -1;  /* hors disque → ignoré */
    }
    (void)g;
    /* p93: scattered samples → table-gradient Perlin (same value, ~4× faster) */
    double v = sampleDoublePerlinScattered(d->dp, x, 0, z) * 10000.0;
    int ok = (v > d->vmin && v < d->vmax);
    if (d->invert) ok = !ok;
    if (ok) {
        d->xsum += (int64_t)x * scale;
        d->zsum += (int64_t)z * scale;
        if (d->out && *d->nout < d->cap) {
            d->out[*d->nout].x = x * scale; d->out[*d->nout].z = z * scale; (*d->nout)++;
        }
        d->n++;
        return 1;
    }
    return 0;
}
typedef struct {
    int rx,rz,rx1,rz1,rx2,rz2,dx,dz,i,dl;
    int step;
    int32_t ax,az,ax2,az2;
    int64_t rmax_sq;
    int32_t at_x,at_z;
    int done;
} SpiralIter;

static void spiral_init(SpiralIter *s,
    int32_t x1,int32_t z1,int32_t x2,int32_t z2,
    int step, int64_t rmax_sq, int32_t at_x, int32_t at_z)
{
    s->ax=x1; s->az=z1; s->ax2=x2; s->az2=z2;
    s->rmax_sq=rmax_sq; s->at_x=at_x; s->at_z=at_z;
    s->step=step;
    /* exactly cubiomes-viewer (_testTreeAt F_SPIRAL): floordiv and >> 1.
     * C '/' truncates toward zero → for negative coordinates the grid range,
     * the spiral centre and therefore its order / extent differed. */
    #define SSA_FLOORDIV(a, b) (((a) / (b)) - (((a) % (b) != 0) && (((a) < 0) != ((b) < 0))))
    s->rx1=SSA_FLOORDIV(x1, step); s->rz1=SSA_FLOORDIV(z1, step);
    s->rx2=SSA_FLOORDIV(x2, step); s->rz2=SSA_FLOORDIV(z2, step);
    s->rx=(s->rx1+s->rx2)>>1; s->rz=(s->rz1+s->rz2)>>1;
    s->dx=1; s->dz=0; s->i=0; s->dl=1; s->done=0;
}

static int spiral_next(SpiralIter *s, int32_t *px, int32_t *pz) {
    while (!s->done) {
        int inx=(s->rx>=s->rx1&&s->rx<=s->rx2);
        int inz=(s->rz>=s->rz1&&s->rz<=s->rz2);
        if (!inx&&!inz) { s->done=1; return 0; }
        int inside=0;
        int32_t bx=s->rx*s->step, bz=s->rz*s->step;
        if (inx&&inz) {
            if (s->rmax_sq) {
                int64_t ddx=(int64_t)(bx-s->at_x), ddz=(int64_t)(bz-s->at_z);
                inside=(ddx*ddx+ddz*ddz<s->rmax_sq);
            } else {
                inside=(bx>=s->ax&&bx<=s->ax2&&bz>=s->az&&bz<=s->az2);
            }
        }
        s->rx+=s->dx; s->rz+=s->dz; s->i++;
        if (s->i==s->dl) {
            s->i=0;
            int tmp=s->dx; s->dx=-s->dz; s->dz=tmp;
            if (s->dz==0) s->dl++;
        }
        if (inside) { *px=bx; *pz=bz; return 1; }
    }
    return 0;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Variant filter check
 * ═══════════════════════════════════════════════════════════════════════════ */
static int variant_pass(SsaEnv *env, const SsaCond *c, int struct_id,
                          int biome_id, int32_t bx, int32_t bz)
{
    if (c->variant_filter == VARIANT_NONE) return 1;
    StructureVariant sv;
    memset(&sv, 0, sizeof(sv));
    getVariant(&sv, struct_id, env->mc, env->seed, bx, bz, biome_id);
    switch (c->variant_filter) {
        case VARIANT_ABANDONED:     return sv.abandoned ? 1 : 0;
        case VARIANT_NOT_ABANDONED: return sv.abandoned ? 0 : 1;
        case VARIANT_BASEMENT:      return sv.basement ? 1 : 0;
        case VARIANT_NO_BASEMENT:   return sv.basement ? 0 : 1;
    }
    return 1;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Stronghold ring prefilter (extracted from search.cpp F_STRONGHOLD)
 * Returns 1 if the area might contain a stronghold, 0 if definitely not.
 * Sets *out_max_ring to limit nextStronghold iteration.
 * ═══════════════════════════════════════════════════════════════════════════ */
static int stronghold_ring_check(int mc, int32_t at_x, int32_t at_z,
                                   const SsaCond *c, int *out_max_ring)
{
    int32_t x1, z1, x2, z2;
    if (c->rmax > 0) {
        x1 = at_x - c->rmax - 8;
        z1 = at_z - c->rmax - 8;
        x2 = at_x + c->rmax - 8;
        z2 = at_z + c->rmax - 8;
    } else {
        x1 = c->x1 + at_x - 8;
        z1 = c->z1 + at_z - 8;
        x2 = c->x2 + at_x - 8;
        z2 = c->z2 + at_z - 8;
    }
    int32_t rx1 = abs(x1), rx2 = abs(x2);
    int32_t rz1 = abs(z1), rz2 = abs(z2);

    double rmin_sq, rmax_sq;
    if (x1 <= 120 && x2 >= -120 && z1 <= 120 && z2 >= -120) {
        rmin_sq = 0;
    } else {
        int32_t xt = (rx1 < rx2 ? rx1 : rx2) - 120;
        int32_t zt = (rz1 < rz2 ? rz1 : rz2) - 120;
        rmin_sq = (double)xt*xt + (double)zt*zt;
    }
    int32_t xt = (rx1 > rx2 ? rx1 : rx2) + 120;
    int32_t zt = (rz1 > rz2 ? rz1 : rz2) + 120;
    rmax_sq = (double)xt*xt + (double)zt*zt;

    *out_max_ring = 0;
    if (mc < MC_1_9) {
        if (rmax_sq < 640.0*640.0 || rmin_sq > 1152.0*1152.0) return 0;
        return 1;
    } else {
        if (rmax_sq < 1408.0*1408.0) return 0;
        double rmin = sqrt(rmin_sq);
        double rmax = sqrt(rmax_sq);
        int r = (int)((rmax - 1408) / 3072);
        if (rmax - rmin < 3072 - 1280) {
            if (rmin > 1408 + 1280 + 3072.0*r) return 0;
        }
        *out_max_ring = r;
        return 1;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Forward declaration
 * ═══════════════════════════════════════════════════════════════════════════ */
static int eval_node(SsaEnv *env, const SsaCond *conds, int n_conds,
                      CondCache *caches,
                      int cond_1idx, int32_t at_x, int32_t at_z);
static int clust_node(SsaEnv *env, const SsaCond *conds, int n_conds, CondCache *caches,
                      const SsaCond *c, int cond_1idx, int32_t at_x, int32_t at_z,
                      const int *kids, int nk);

/* ═══════════════════════════════════════════════════════════════════════════
 * struct_collect — scan the condition area for valid instances of a structure
 * (same order and checks as cubiomes-viewer testCondAt). Stores up to `maxn`
 * positions in `out`, stops once `stop_at` instances are found (stop_at <= 0:
 * scan everything), accumulates the coordinate sums when xs/zs are given.
 * Returns the number of instances found, or -1 if the structure is unknown.
 * ═══════════════════════════════════════════════════════════════════════════ */
static int struct_collect(SsaEnv *env, const SsaCond *c, int cond_idx,
                          CondCache *caches, int32_t at_x, int32_t at_z,
                          Pos *out, int maxn, int stop_at,
                          int64_t *xs, int64_t *zs)
{
    int32_t x1, z1, x2, z2; int64_t rmax_sq;
    if (c->rmax > 0) {
        int32_t rb = c->rmax - 1;
        x1 = at_x - rb; z1 = at_z - rb; x2 = at_x + rb; z2 = at_z + rb;
        rmax_sq = (int64_t)rb*rb + 1;
    } else {
        x1 = at_x + c->x1; z1 = at_z + c->z1; x2 = at_x + c->x2; z2 = at_z + c->z2;
        rmax_sq = 0;
    }
    int stype = c->struct_type;
    int dim = (c->ctype == CTYPE_STRUCTURE_NETHER) ? DIM_NETHER :
              (c->ctype == CTYPE_STRUCTURE_END)    ? DIM_END    : DIM_OVERWORLD;
    CondCache *cache = &caches[cond_idx - 1];
    if (!cache->sconf_ready) {
        if (!getStructureConfig(stype, env->mc, &cache->sconf)) return -1;
        cache->sconf_ready = 1;
    }
    int region_size = cache->sconf.regionSize;
    if (region_size <= 0) return -1;
    int32_t rx1, rz1, rx2, rz2;
    if (region_size == 32) {
        rx1 = x1 >> 9; rz1 = z1 >> 9; rx2 = x2 >> 9; rz2 = z2 >> 9;
    } else if (region_size == 1) {
        rx1 = x1 >> 4; rz1 = z1 >> 4; rx2 = x2 >> 4; rz2 = z2 >> 4;
    } else {
        int csz = region_size << 4;
        rx1 = (x1 >= 0) ? (x1 / csz) : -((-x1 + csz - 1) / csz);
        rz1 = (z1 >= 0) ? (z1 / csz) : -((-z1 + csz - 1) / csz);
        rx2 = (x2 >= 0) ? (x2 / csz) : -((-x2 + csz - 1) / csz);
        rz2 = (z2 >= 0) ? (z2 / csz) : -((-z2 + csz - 1) / csz);
    }
    env_set_dim(env, dim);
    int hits = 0;
    for (int32_t rz = rz1; rz <= rz2; rz++) {
        for (int32_t rx = rx1; rx <= rx2; rx++) {
            Pos p;
            if (!getStructurePos(stype, env->mc, env->seed, rx, rz, &p))
                continue;
            if (c->skip_ref && p.x == at_x && p.z == at_z)
                continue;
            int inside;
            if (rmax_sq) {
                int64_t dx = p.x - at_x, dz = p.z - at_z;
                inside = (dx*dx + dz*dz < rmax_sq);
            } else {
                inside = (p.x >= x1 && p.x <= x2 && p.z >= z1 && p.z <= z2);
            }
            if (!inside) continue;
            int biome_id = isViableStructurePos(stype, &env->g, p.x, p.z, 0);
            if (!biome_id) continue;
            if (stype == End_City) {
                if (!env->sn_end_ready) {
                    initSurfaceNoise(&env->sn_end, DIM_END, env->seed);
                    env->sn_end_ready = 1;
                }
                if (!isViableEndCityTerrain(&env->g, &env->sn_end, p.x, p.z))
                    continue;
            }
            if (c->variant_filter != VARIANT_NONE &&
                !variant_pass(env, c, stype, biome_id, p.x, p.z))
                continue;
            if (env->mc >= MC_1_18 && dim == DIM_OVERWORLD &&
                !isViableStructureTerrain(stype, &env->g, p.x, p.z))
                continue;
            if (hits < maxn) out[hits] = p;
            if (xs) { *xs += p.x; *zs += p.z; }
            hits++;
            if (stop_at > 0 && hits >= stop_at) goto done;
        }
    }
done:
    env_set_dim(env, DIM_OVERWORLD);
    return hits;
}

#define STRUCT_SPLIT_MAX 4096

/* ═══════════════════════════════════════════════════════════════════════════
 * eval_condition — evaluate one node, return 1 on pass, 0 on fail
 * ═══════════════════════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════════════════════
 * Locate climate center — coarse flood fill of ONE climate parameter (01/10)
 *
 * The world is cut into cells of `s` blocks (c->biome_scale, 4…1024). Starting
 * from the cell holding the reference position, the 4-connected cells whose
 * value is in the range (outside it with « invert ») form the region; it is
 * explored only inside the condition's area (default ±128 cells). Each cell is
 * sampled once, at its centre block, at the climate's 1:4 resolution, with the
 * condition's octave limit — the same sampling as « Locate climate extreme ».
 *   pass  : region ≥ need cells (c->biome_min_size)
 *           and, with a maximum (c->biome_tolerance > 0), ≤ max cells and not
 *           cut by the area border (its real size would be unknown)
 *   push  : the centroid of the region (centre block of the mean cell) — the
 *           children are tested there. Without children (c->min_count == 0)
 *           and without a maximum, the fill stops as soon as `need` cells are
 *           reached and the start cell is pushed (same pass/fail, much faster).
 * Memory: a bitmap and a queue allocated for this call only (thread-safe, no
 * shared state); Stop is checked every 4 096 cells.
 * ═══════════════════════════════════════════════════════════════════════════ */
#define CA_DEFAULT_HALF 128              /* cells each side when the area is 0 */
#define CA_MAX_CELLS    (1 << 20)        /* exploration window cap (1024 × 1024) */

static inline int64_t ca_floordiv(int64_t a, int64_t b) {
    int64_t q = a / b;
    return (a % b != 0 && ((a < 0) != (b < 0))) ? q - 1 : q;
}

static int eval_climate_area(SsaEnv *env, const SsaCond *c, int cond_idx,
                             int32_t at_x, int32_t at_z)
{
    int para = c->climate_para;
    if (para < 0 || para >= NP_MAX) return 0;
    int64_t s = c->biome_scale;
    if (s < 4) s = 4;
    if (s > 1024) s = 1024;
    const DoublePerlinNoise *dp = clim_dp(env, para, c->climate_nmax);
    double vmin = (c->climate_mode & 4) ? c->climate_vmin : -INFINITY;
    double vmax = (c->climate_mode & 8) ? c->climate_vmax : +INFINITY;
    int inv = c->climate_invert;

    /* exploration window, in cells */
    int64_t ci0 = ca_floordiv(at_x, s), cj0 = ca_floordiv(at_z, s);
    int64_t gx1, gz1, gx2, gz2, rmax_sq = 0;
    if (c->rmax > 0) {
        int64_t rb = c->rmax - 1;
        gx1 = ca_floordiv(at_x - rb, s); gx2 = ca_floordiv(at_x + rb, s);
        gz1 = ca_floordiv(at_z - rb, s); gz2 = ca_floordiv(at_z + rb, s);
        rmax_sq = rb * rb + 1;
    } else if (c->x1 == c->x2 && c->z1 == c->z2) {
        gx1 = ci0 - CA_DEFAULT_HALF; gx2 = ci0 + CA_DEFAULT_HALF;
        gz1 = cj0 - CA_DEFAULT_HALF; gz2 = cj0 + CA_DEFAULT_HALF;
    } else {
        int64_t a = (int64_t)at_x + c->x1, b = (int64_t)at_x + c->x2;
        int64_t d = (int64_t)at_z + c->z1, e = (int64_t)at_z + c->z2;
        gx1 = ca_floordiv(a < b ? a : b, s); gx2 = ca_floordiv(a < b ? b : a, s);
        gz1 = ca_floordiv(d < e ? d : e, s); gz2 = ca_floordiv(d < e ? e : d, s);
    }
    /* the start cell is always inside; the window is capped around it */
    if (ci0 < gx1) gx1 = ci0;
    if (ci0 > gx2) gx2 = ci0;
    if (cj0 < gz1) gz1 = cj0;
    if (cj0 > gz2) gz2 = cj0;
    while ((gx2 - gx1 + 1) * (gz2 - gz1 + 1) > CA_MAX_CELLS) {
        if (gx2 - gx1 >= gz2 - gz1) {
            if (ci0 - gx1 > gx2 - ci0) gx1++; else gx2--;
        } else {
            if (cj0 - gz1 > gz2 - cj0) gz1++; else gz2--;
        }
    }
    int64_t W = gx2 - gx1 + 1, H = gz2 - gz1 + 1;

#define CA_IN_WINDOW(i, j) ((i) >= gx1 && (i) <= gx2 && (j) >= gz1 && (j) <= gz2 && \
        (!rmax_sq || ({ int64_t _dx = (i) * s + s / 2 - at_x, _dz = (j) * s + s / 2 - at_z; \
                        _dx * _dx + _dz * _dz < rmax_sq; })))
#define CA_SAMPLE_OK(i, j) ({ double _v = sampleDoublePerlinScattered(dp, \
        (double)(((i) * s + s / 2) >> 2), 0, (double)(((j) * s + s / 2) >> 2)) * 10000.0; \
        int _in = (_v >= vmin && _v <= vmax); inv ? !(_v > vmin && _v < vmax) : _in; })

    /* the start cell first: rejects most seeds for the price of one sample */
    if (!CA_SAMPLE_OK(ci0, cj0)) return 0;

    int64_t need = c->biome_min_size > 0 ? c->biome_min_size : 1;
    int64_t maxc = c->biome_tolerance > 0 ? c->biome_tolerance : 0;
    int want_center = c->min_count > 0 || maxc > 0;
    if (need <= 1 && !want_center) { env_push(env, cond_idx, (int32_t)(ci0 * s + s / 2),
                                              (int32_t)(cj0 * s + s / 2)); return 1; }

    int64_t ncell = W * H;
    uint8_t *seen = (uint8_t *)calloc((size_t)((ncell + 7) >> 3), 1);
    int32_t *q = NULL; int64_t qcap = 4096;
    q = (int32_t *)malloc((size_t)qcap * sizeof(int32_t));
    if (!seen || !q) { free(seen); free(q); return 0; }
#define CA_MARK(k)  (seen[(k) >> 3] |= (uint8_t)(1u << ((k) & 7)))
#define CA_SEEN(k)  (seen[(k) >> 3] & (1u << ((k) & 7)))

    int64_t k0 = (cj0 - gz1) * W + (ci0 - gx1);
    CA_MARK(k0);
    int64_t qh = 0, qt = 0;
    q[qt++] = (int32_t)k0;
    int64_t count = 0, sx = 0, sz = 0;
    int border = 0, ok = 0, aborted = 0;
    volatile int *stop = env->stop_flag;
    static const int DI[4] = {1, -1, 0, 0}, DJ[4] = {0, 0, 1, -1};
    while (qh < qt) {
        int64_t k = q[qh++];
        int64_t i = gx1 + k % W, j = gz1 + k / W;
        count++; sx += i; sz += j;
        if (!want_center && count >= need) { ok = 1; break; }
        if (maxc && count > maxc) break;                  /* too big */
        if (stop && (count & 4095) == 0 && *stop) { aborted = 1; break; }
        for (int d = 0; d < 4; d++) {
            int64_t ni = i + DI[d], nj = j + DJ[d];
            if (!CA_IN_WINDOW(ni, nj)) { border = 1; continue; }
            int64_t nk = (nj - gz1) * W + (ni - gx1);
            if (CA_SEEN(nk)) continue;
            CA_MARK(nk);
            if (!CA_SAMPLE_OK(ni, nj)) continue;
            if (qt >= qcap) {
                int64_t nc = qcap * 2;
                int32_t *t = (int32_t *)realloc(q, (size_t)nc * sizeof(int32_t));
                if (!t) { aborted = 1; break; }
                q = t; qcap = nc;
            }
            q[qt++] = (int32_t)nk;
        }
        if (aborted) break;
    }
    if (!ok && !aborted && qh >= qt) {                    /* whole region seen */
        ok = count >= need && (!maxc || (count <= maxc && !border));
    }
    free(seen); free(q);
#undef CA_MARK
#undef CA_SEEN
#undef CA_IN_WINDOW
#undef CA_SAMPLE_OK
    if (!ok || aborted) return 0;
    if (want_center) {
        /* centre block of the mean cell (floor division keeps negatives right) */
        int64_t mi = ca_floordiv(sx, count), mj = ca_floordiv(sz, count);
        env_push(env, cond_idx, (int32_t)(mi * s + s / 2), (int32_t)(mj * s + s / 2));
    } else {
        env_push(env, cond_idx, (int32_t)(ci0 * s + s / 2), (int32_t)(cj0 * s + s / 2));
    }
    return 1;
}

static int eval_condition(SsaEnv *env, const SsaCond *c, int cond_idx,
                           int32_t at_x, int32_t at_z,
                           const SsaCond *all_conds, int n_conds,
                           CondCache *caches)
{
    /* Fast-path ultra-prioritaire : climate_minmax sur un point unique
     * (rmax==0, x1==x2, z1==z2), non-inversé. C'est la condition la plus
     * fréquente (≈2/graine dans une session climat). On évite tout le calcul
     * commun de zone (x1/z1/x2/z2/rmax_sq + 2 divisions) et le dispatch du gros
     * switch : juste cache climat + 1 sample + test de borne. */
    if (c->ctype == CTYPE_CLIMATE_MINMAX && c->rmax == 0
        && c->x1 == c->x2 && c->z1 == c->z2 && !c->climate_invert) {
        int para = c->climate_para;
        if (para < 0 || para >= NP_MAX) return 0;
        int nmax = c->climate_nmax;
        const DoublePerlinNoise *dpn = clim_dp(env, para, nmax);
        int32_t rx = (at_x + c->x1) >> 2, rz = (at_z + c->z1) >> 2;
        double p = sampleDoublePerlinScattered(dpn, rx, 0, rz) * 10000.0;
        double vmin = (c->climate_mode & 4) ? c->climate_vmin : -INFINITY;
        double vmax = (c->climate_mode & 8) ? c->climate_vmax : +INFINITY;
        if (!(p >= vmin && p <= vmax)) return 0;
        env_push(env, cond_idx, rx << 2, rz << 2);
        return 1;
    }

    int32_t x1,z1,x2,z2; int64_t rmax_sq;
    if (c->rmax > 0) {
        int32_t rb = c->rmax - 1;
        x1 = at_x - rb; z1 = at_z - rb;
        x2 = at_x + rb; z2 = at_z + rb;
        rmax_sq = (int64_t)rb*rb + 1;
    } else {
        x1 = at_x + c->x1; z1 = at_z + c->z1;
        x2 = at_x + c->x2; z2 = at_z + c->z2;
        rmax_sq = 0;
    }
    /* area centre as cubiomes-viewer: (x1 + x2) >> 1 (floor, not truncation) */
    int32_t cx_def = (int32_t)(((int64_t)x1 + x2) >> 1), cz_def = (int32_t)(((int64_t)z1 + z2) >> 1);

    switch (c->ctype) {

    case CTYPE_CLIMATE_AREA:
        return eval_climate_area(env, c, cond_idx, at_x, at_z);

    case CTYPE_SCALE_TO_NETHER:
        env_push(env, cond_idx, at_x/8, at_z/8);
        return 1;
    case CTYPE_SCALE_TO_OVERWORLD:
        env_push(env, cond_idx, at_x*8, at_z*8);
        return 1;
    case CTYPE_SPIRAL:
        env_push(env, cond_idx, cx_def, cz_def);
        return 1;

    case CTYPE_LOGIC_OR: {
        int has_child = 0;
        for (int i = 0; i < n_conds; i++) {
            const SsaCond *ch = &all_conds[i];
            if (!ch->cond_idx || ch->parent_idx != cond_idx) continue;
            has_child = 1;
            /* on appelle eval_node (et non eval_condition) pour que chaque
             * alternative du OR évalue AUSSI ses enfants AND (ex. un height
             * sous un Locate W). Sinon les enfants ne sont jamais testés. */
            if (eval_node(env, all_conds, n_conds, caches,
                           ch->cond_idx, at_x, at_z)) {
                env_push(env, cond_idx, at_x, at_z);
                return 1;
            }
        }
        /* cubiomes-viewer: « empty ORs are ignored » — an OR without any
         * (enabled) alternative passes (before: rejected every seed) */
        if (!has_child) { env_push(env, cond_idx, at_x, at_z); return 1; }
        return 0; }
    case CTYPE_LOGIC_NOT: {
        int has_child = 0;
        for (int i = 0; i < n_conds; i++) {
            const SsaCond *ch = &all_conds[i];
            if (!ch->cond_idx || ch->parent_idx != cond_idx) continue;
            has_child = 1;
            if (eval_node(env, all_conds, n_conds, caches,
                           ch->cond_idx, at_x, at_z))
                return 0;
        }
        if (!has_child) return 0;
        env_push(env, cond_idx, at_x, at_z);
        return 1; }

    case CTYPE_SPAWN: {
        env_set_dim(env, DIM_OVERWORLD);
        Pos p = getSpawn(&env->g);
        if (rmax_sq) {
            int64_t dx = p.x - at_x, dz = p.z - at_z;
            if (dx*dx + dz*dz >= rmax_sq) return 0;
        } else if (p.x < x1 || p.x > x2 || p.z < z1 || p.z > z2) {
            return 0;
        }
        if (c->skip_ref && p.x == at_x && p.z == at_z) return 0;   /* CV skipref */
        env_push(env, cond_idx, p.x, p.z);
        return 1; }

    case CTYPE_FIRST_STRONGHOLD: {
        StrongholdIter it;
        Pos p = initFirstStronghold(&it, env->mc, env->seed);
        if (c->rmax > 0) {
            int64_t dx = p.x - at_x, dz = p.z - at_z;
            int64_t rmax_v = (int64_t)(c->rmax-1)*(c->rmax-1) + 1;
            if (dx*dx + dz*dz > rmax_v) return 0;
        } else if (p.x < x1 || p.x > x2 || p.z < z1 || p.z > z2) {
            return 0;
        }
        if (c->skip_ref && p.x == at_x && p.z == at_z) return 0;   /* CV skipref */
        env_push(env, cond_idx, p.x, p.z);
        return 1; }

    case CTYPE_STRONGHOLD: {
        int min_count = c->min_count <= 0 ? 1 : c->min_count;
        int exclude = c->exclude;
        int max_ring = 0;
        if (!stronghold_ring_check(env->mc, at_x, at_z, c, &max_ring)) {
            if (exclude) { env_push(env, cond_idx, cx_def, cz_def); return 1; }
            return 0;
        }
        env_set_dim(env, DIM_OVERWORLD);
        StrongholdIter it;
        initFirstStronghold(&it, env->mc, env->seed);
        int hits = 0;
        Pos first = {cx_def, cz_def};
        int64_t xsum = 0, zsum = 0;
        while (nextStronghold(&it, &env->g) > 0) {
            int32_t spx = it.pos.x, spz = it.pos.z;
            int inside;
            if (rmax_sq) {
                int64_t dx = spx - at_x, dz = spz - at_z;
                inside = (dx*dx + dz*dz < rmax_sq);
            } else {
                inside = (spx >= x1 && spx <= x2 && spz >= z1 && spz <= z2);
            }
            if (c->skip_ref && spx == at_x && spz == at_z) inside = 0;  /* CV skipref */
            if (inside) {
                if (!hits) first = it.pos;
                xsum += spx; zsum += spz; hits++;
                if (exclude) return 0;
                if (hits >= min_count) {
                    env_push(env, cond_idx, first.x, first.z);
                    return 1;
                }
            }
            if (it.ringnum > max_ring) break;
        }
        if (exclude) { env_push(env, cond_idx, cx_def, cz_def); return 1; }
        if (hits < min_count) return 0;
        env_push(env, cond_idx, (int32_t)(xsum/hits), (int32_t)(zsum/hits));
        return 1; }

    case CTYPE_SLIME: {
        int min_count = c->min_count <= 0 ? 1 : c->min_count;
        int exclude = c->exclude;
        int32_t cx1 = x1 >> 4, cz1 = z1 >> 4;
        int32_t cx2 = x2 >> 4, cz2 = z2 >> 4;
        int hits = 0;
        int64_t xsum = 0, zsum = 0;
        for (int32_t rcz = cz1; rcz <= cz2; rcz++) {
            for (int32_t rcx = cx1; rcx <= cx2; rcx++) {
                if (!is_slime_chunk(env->seed, rcx, rcz)) continue;
                int32_t bx = (rcx << 4) + 8, bz = (rcz << 4) + 8;
                if (rmax_sq) {
                    int64_t dx = bx - at_x, dz = bz - at_z;
                    if (dx*dx + dz*dz >= rmax_sq) continue;
                }
                hits++;
                xsum += bx; zsum += bz;
                if (exclude) return 0;
                if (hits >= min_count) goto slime_done;
            }
        }
    slime_done:
        if (exclude) { env_push(env, cond_idx, cx_def, cz_def); return 1; }
        if (hits < min_count) return 0;
        env_push(env, cond_idx, (int32_t)(xsum/hits), (int32_t)(zsum/hits));
        return 1; }

    case CTYPE_MINESHAFT: {
        int min_count = c->min_count <= 0 ? 1 : c->min_count;
        int exclude = c->exclude;
        int32_t cx1 = x1 >> 4, cz1 = z1 >> 4;
        int32_t cx2 = x2 >> 4, cz2 = z2 >> 4;
        int cw = cx2 - cx1 + 1;
        int ch_ = cz2 - cz1 + 1;
        if (cw < 1) cw = 1; if (ch_ < 1) ch_ = 1;
        Pos buf[128];
        (void)cw; (void)ch_;
        /* getMineshafts(mc, seed, cx0, cz0, cx1, cz1, …) takes the LAST chunk
         * (inclusive), not a width: the width was passed → wrong chunk range
         * (e.g. chunks −3…6 instead of −3…2), mineshafts missed / invented. */
        int n = getMineshafts(env->mc, env->seed, cx1, cz1, cx2, cz2,
                                buf, (int)(sizeof(buf)/sizeof(buf[0])));
        if (n > (int)(sizeof(buf)/sizeof(buf[0]))) n = (int)(sizeof(buf)/sizeof(buf[0]));
        int hits = 0;
        int64_t xsum = 0, zsum = 0;
        for (int i = 0; i < n; i++) {
            if (rmax_sq) {
                int64_t dx = buf[i].x - at_x, dz = buf[i].z - at_z;
                if (dx*dx + dz*dz >= rmax_sq) continue;
            }
            hits++;
            xsum += buf[i].x; zsum += buf[i].z;
        }
        if (exclude) {
            if (hits > 0) return 0;
            env_push(env, cond_idx, cx_def, cz_def);
            return 1;
        }
        if (hits < min_count) return 0;
        env_push(env, cond_idx, (int32_t)(xsum/hits), (int32_t)(zsum/hits));
        return 1; }

    case CTYPE_STRUCTURE_OW:
    case CTYPE_STRUCTURE_NETHER:
    case CTYPE_STRUCTURE_END: {
        /* Leaf use (or no dependent conditions): cubiomes-viewer keeps the
         * first instance as the position. Branching over instances / centroid
         * for nodes WITH children is handled in eval_node (eval_struct_node). */
        int min_count = c->min_count <= 0 ? 1 : c->min_count;
        Pos first;
        int hits = struct_collect(env, c, cond_idx, caches, at_x, at_z,
                                  &first, 1, c->exclude ? 1 : min_count, NULL, NULL);
        if (hits < 0) return 0;
        if (c->exclude) {
            if (hits) return 0;
            env_push(env, cond_idx, cx_def, cz_def);
            return 1;
        }
        if (hits < min_count) return 0;
        env_push(env, cond_idx, first.x, first.z);
        return 1; }

    case CTYPE_BIOME_FILTER: {
        int scale = c->biome_scale > 0 ? c->biome_scale : 4;
        int s_shift;
        switch (scale) {
            case 1:   s_shift = 0; break;
            case 4:   s_shift = 2; break;
            case 16:  s_shift = 4; break;
            case 64:  s_shift = 6; break;
            case 256: s_shift = 8; break;
            default:  return 0;
        }
        int dim = c->biome_dim;
        env_set_dim(env, dim);

        CondCache *cache = &caches[cond_idx - 1];
        if (!cache->bf_ready) {
            memset(&cache->bf, 0, sizeof(BiomeFilter));
            /* biome_match_any: bit 0 = match any, bit 1 = cubiomes-viewer
             * « approximate » (FLG_APPROX). Deliberately NOT applied: cubiomes'
             * BF_APPROX tests a RANDOM subset of the cells (rand()), so it
             * misses biomes at random — SSA keeps the exact test (it finds a
             * superset of what cubiomes-viewer finds with the option). */
            uint32_t bff = BF_FORCED_OCEAN;
            if (c->biome_match_any & 1) {
                /* Match-any: any of the "included" biomes is enough.
                 * Cubiomes-viewer convention: pass them as matchany, no
                 * required list. Exclusions still apply. */
                setupBiomeFilter(&cache->bf, env->mc, bff,
                                  NULL, 0,
                                  c->biomes_excl, c->n_excl,
                                  c->biomes_incl, c->n_incl);
            } else {
                /* Match-all: every "included" biome must be present. */
                setupBiomeFilter(&cache->bf, env->mc, bff,
                                  c->biomes_incl, c->n_incl,
                                  c->biomes_excl, c->n_excl,
                                  NULL, 0);
            }
            cache->bf_ready = 1;
        }
        Range r;
        r.scale = scale;
        r.x = x1 >> s_shift; r.z = z1 >> s_shift;
        /* cubiomes-viewer extent (x2>>s)-(x1>>s)+1 — was ((x2-x1)>>s)+1,
         * one cell off whenever x1 is not on the grid */
        r.sx = (x2 >> s_shift) - r.x + 1; if (r.sx < 1) r.sx = 1;
        r.sz = (z2 >> s_shift) - r.z + 1; if (r.sz < 1) r.sz = 1;
        r.y = (s_shift > 0) ? (c->biome_y >> 2) : c->biome_y;
        r.sy = 1;
        int ok = fast_check_biomes(env, cache, r, dim);
        if (ok < 0)
            ok = checkForBiomes(&env->g, NULL, r, dim, env->seed,
                                  &cache->bf, (volatile char*)env->stop_flag) > 0;
        env_set_dim(env, DIM_OVERWORLD);
        if (c->exclude) {
            if (ok) return 0;
            env_push(env, cond_idx, cx_def, cz_def); return 1;
        }
        if (!ok) return 0;
        env_push(env, cond_idx, cx_def, cz_def);
        return 1; }

    case CTYPE_BIOME_SAMPLE: {
        if (c->confidence <= 0 || c->confidence >= 1) return 0;
        if (c->coverage <= 0 || c->coverage > 1) return 0;
        int scale = c->biome_scale > 0 ? c->biome_scale : 4;
        int s_shift = 2;
        switch (scale) {
            case 1: s_shift=0; break; case 4: s_shift=2; break;
            case 16:s_shift=4; break; case 64:s_shift=6; break;
            case 256:s_shift=8; break;
        }
        env_set_dim(env, DIM_OVERWORLD);
        Range r;
        r.scale = scale;
        r.x = x1 >> s_shift; r.z = z1 >> s_shift;
        /* cubiomes-viewer extent (x2>>s)-(x1>>s)+1 — was ((x2-x1)>>s)+1,
         * one cell off whenever x1 is not on the grid */
        r.sx = (x2 >> s_shift) - r.x + 1; if (r.sx < 1) r.sx = 1;
        r.sz = (z2 >> s_shift) - r.z + 1; if (r.sz < 1) r.sz = 1;
        r.y = (s_shift > 0) ? (c->biome_y >> 2) : c->biome_y;
        r.sy = 1;
        MonteData d;
        d.incl = c->biomes_incl; d.n_incl = c->n_incl;
        d.excl = c->biomes_excl; d.n_excl = c->n_excl;
        d.xsum = d.zsum = d.n = 0;
        d.at_x = at_x; d.at_z = at_z; d.rmax_sq = rmax_sq;
        d.out = env->sample_out; d.nout = &env->sample_n; d.cap = env->sample_cap;
        env->sample_out = NULL;           /* one call only (not the children) */
        d.fast = 0; d.bp = NULL; d.alive = 0;
        if (env->mc >= MC_1_18) {
            Generator *g = &env->g;
            if (g->dim != DIM_OVERWORLD || g->seed != env->seed)
                applySeed(g, DIM_OVERWORLD, env->seed);
            if (g->bn.nptype == -1) {
                CondCache *cache = &caches[cond_idx - 1];
                if (!cache->bp_state) {
                    uint64_t bb = 0, mm = 0; int bad = 0;
                    for (int i = 0; i < c->n_incl + c->n_excl; i++) {
                        int id = i < c->n_incl ? c->biomes_incl[i] : c->biomes_excl[i - c->n_incl];
                        if (id < 0 || id >= 192 || (id >= 64 && id < 128)) { bad = 1; break; }
                        if (id < 128) bb |= 1ULL << id; else mm |= 1ULL << (id - 128);
                    }
                    if (bad) cache->bp_state = -1;
                    else cache_setup_prune(cache, env->mc, bb, mm);
                }
                d.fast = 1;
                if (cache->bp_state > 0) {
                    d.bp = &cache->bp; d.alive = (1u << cache->bp.n) - 1;
                }
            }
        }
        uint64_t rng; setSeed(&rng, env->seed);
        int ok = monteCarloBiomes(&env->g, r, &rng, c->coverage, c->confidence,
                                    monte_eval, &d) == 1;
        if (!ok) return 0;
        int32_t ox = cx_def, oz = cz_def;
        /* cubiomes-viewer: mean of the sampled 1:4 cells, +2 = cell centre */
        if (d.n > 0) { ox = (int32_t)(d.xsum/d.n) + 2; oz = (int32_t)(d.zsum/d.n) + 2; }
        env_push(env, cond_idx, ox, oz);
        return 1; }

    case CTYPE_BIOME_CENTER: {
        int min_count = c->min_count <= 0 ? 1 : c->min_count;
        int exclude = c->exclude;
        env_set_dim(env, DIM_OVERWORLD);
        Range r;
        r.scale = 4;
        r.x = x1 >> 2; r.z = z1 >> 2;
        r.sx = (x2 >> 2) - r.x + 1; if (r.sx < 1) r.sx = 1;   /* viewer extent */
        r.sz = (z2 >> 2) - r.z + 1; if (r.sz < 1) r.sz = 1;
        r.y = c->biome_y >> 2; r.sy = 1;
        Pos centers[64];
        int request = exclude ? 1 : (min_count < 64 ? min_count : 64);
        int n = getBiomeCenters(centers, NULL, request,
                                  &env->g, r,
                                  c->biome_id, c->biome_min_size,
                                  c->biome_tolerance, NULL);
        if (n > request) n = request;
        if (c->skip_ref && !exclude) {          /* CV: the origin instance is removed */
            for (int i = 0; i < n; i++)
                if (centers[i].x == at_x && centers[i].z == at_z) { centers[i] = centers[n-1]; n--; break; }
        }
        int hits = 0;
        Pos first = {cx_def, cz_def};
        if (rmax_sq) {
            for (int i = 0; i < n; i++) {
                int64_t dx = centers[i].x - at_x;
                int64_t dz = centers[i].z - at_z;
                if (dx*dx + dz*dz < rmax_sq) {
                    if (!hits) first = centers[i];
                    hits++;
                }
            }
        } else {
            hits = n;
            if (n > 0) first = centers[0];
        }
        if (exclude) {
            if (hits > 0) return 0;
            env_push(env, cond_idx, cx_def, cz_def);
            return 1;
        }
        if (hits < min_count) return 0;
        env_push(env, cond_idx, first.x, first.z);
        return 1; }

    case CTYPE_NOISE_SAMPLE: {
        /* F_NOISE_SAMPLE exact : Monte-Carlo sur un paramètre climatique avec
         * coverage/confidence (réplique search.cpp). Bien plus sélectif que
         * l'ancien mapping "∃ un point" — c'est ce qui permet de rejeter la
         * quasi-totalité des graines AVANT le spawn coûteux. */
        if (c->confidence <= 0 || c->confidence >= 1) return 0;
        if (c->coverage  <= 0 || c->coverage  >  1) return 0;
        int para = c->climate_para;
        if (para < 0 || para >= NP_MAX) return 0;
        int nmax = c->climate_nmax;
        /* Climate noise from the per-(para, nmax) cache, like the climate
         * conditions — NOT re-seeded into env->g.bn: that turned the biome
         * generator into a single-parameter noise (nptype = para), and every
         * biome condition evaluated later for the same seed (a biome sample
         * under a spiral, at the next positions) read wrong biomes. Seen on
         * « large cherry islands »: 0 results, 3 of cubiomes-viewer's seeds
         * rejected (their cherry grove was found only at the ~50 000th spiral
         * position, after many noise samples). */
        const DoublePerlinNoise *ndp = clim_dp(env, para, nmax);
        int s_shift = 2;  /* scale 4, comme CV (s=2) */
        Range r;
        r.scale = 1 << s_shift;
        r.x = x1 >> s_shift; r.z = z1 >> s_shift;
        /* cubiomes-viewer extent (x2>>s)-(x1>>s)+1 — was ((x2-x1)>>s)+1,
         * one cell off whenever x1 is not on the grid */
        r.sx = (x2 >> s_shift) - r.x + 1; if (r.sx < 1) r.sx = 1;
        r.sz = (z2 >> s_shift) - r.z + 1; if (r.sz < 1) r.sz = 1;
        r.y = c->biome_y >> 2; r.sy = 1;
        MonteNoiseData d;
        d.dp = ndp;
        d.para = para;
        d.vmin = (c->climate_mode & 4) ? c->climate_vmin : -INFINITY;
        d.vmax = (c->climate_mode & 8) ? c->climate_vmax : +INFINITY;
        d.invert = c->climate_invert;
        d.xsum = d.zsum = d.n = 0;
        d.at_x = at_x; d.at_z = at_z; d.rmax_sq = rmax_sq;
        d.out = env->sample_out; d.nout = &env->sample_n; d.cap = env->sample_cap;
        env->sample_out = NULL;           /* one call only (not the children) */
        uint64_t rng; setSeed(&rng, env->seed);
        int ok = monteCarloBiomes(&env->g, r, &rng, c->coverage, c->confidence,
                                    monte_noise_eval, &d) == 1;
        if (!ok) return 0;
        int32_t ox = cx_def, oz = cz_def;
        /* cubiomes-viewer: mean of the sampled 1:4 cells, +2 = cell centre */
        if (d.n > 0) { ox = (int32_t)(d.xsum/d.n) + 2; oz = (int32_t)(d.zsum/d.n) + 2; }
        env_push(env, cond_idx, ox, oz);
        return 1; }

    case CTYPE_CLIMATE_MINMAX: {
        int para = c->climate_para;
        if (para < 0 || para >= NP_MAX) return 0;
        int nmax = c->climate_nmax;
        /* Cache lookup multi-nmax : on garde un slot par nmax distinct, donc
         * alterner nmax=1/2/full sur le même para ne casse plus le cache. */
        const DoublePerlinNoise *cdp = clim_dp(env, para, nmax);
        int rx = x1 >> 2, rz = z1 >> 2;
        int rw = (x2 >> 2) - rx + 1; if (rw < 1) rw = 1;   /* viewer extent */
        int rh = (z2 >> 2) - rz + 1; if (rh < 1) rh = 1;
        double vmin = (c->climate_mode & 4) ? c->climate_vmin : -INFINITY;
        double vmax = (c->climate_mode & 8) ? c->climate_vmax : +INFINITY;
        int loc_min = (c->climate_mode & 1);
        int loc_max = (c->climate_mode & 2);

        /* ── Fast-path point unique (rw==rh==1) ──────────────────────────────
         * Cas ultra-fréquent (la plupart des conditions climat de session sont
         * des points), notamment la condition racine évaluée sur 100% des
         * graines. On évite tout l'appareillage getParaRange/min-max : un seul
         * sampleDoublePerlin suffit. evalmin == evalmax == p pour un point. */
        if (rw == 1 && rh == 1) {
            /* getParaRange applique un facteur ×10000 sur l'échantillon avant
             * de le comparer aux bornes ; on reproduit exactement la même
             * convention ici pour rester bit-identique au chemin général. */
            double p = sampleDoublePerlinScattered(cdp, rx, 0, rz) * 10000.0;
            int pass1;
            if (c->climate_invert) pass1 = !(p > vmin && p < vmax);
            else                   pass1 =  (p >= vmin && p <= vmax);
            if (!pass1) return 0;
            env_push(env, cond_idx, rx << 2, rz << 2);
            return 1;
        }

        /* ── Early-exit sur grande zone ──────────────────────────────────────
         * Balayer 1M+ de points pour le min/max EXACT est ruineux (260µs+).
         * Pour les cas simples non-invertis sur une seule borne, on n'a pas
         * besoin du min/max exact — juste de savoir si la condition tient :
         *   - locate MIN + test UPPER (mode 9) : passe ssi ∃ point ≤ vmax
         *     (le min de la zone ≤ vmax). Dès qu'un point ≤ vmax est vu → OK.
         *   - locate MIN + test LOWER : passe ssi TOUT point ≥ vmin (min ≥ vmin).
         *     Dès qu'un point < vmin est vu → échec.
         *   - locate MAX + test LOWER (mode 6) : passe ssi ∃ point ≥ vmin.
         *   - locate MAX + test UPPER : passe ssi TOUT point ≤ vmax.
         * Ces tests exacts s'arrêtent au premier point décisif. On garde la
         * position de ce point comme résultat (suffisant pour les conditions
         * de localisation imbriquées). Le balayage complet n'est conservé que
         * pour les cas ambigus (deux bornes, inversion, petite zone). */
        int single_min_upper = (!c->climate_invert && loc_min && !loc_max
                                 && (c->climate_mode & 8) && !(c->climate_mode & 4));
        int single_min_lower = (!c->climate_invert && loc_min && !loc_max
                                 && (c->climate_mode & 4) && !(c->climate_mode & 8));
        int single_max_lower = (!c->climate_invert && loc_max && !loc_min
                                 && (c->climate_mode & 4) && !(c->climate_mode & 8));
        int single_max_upper = (!c->climate_invert && loc_max && !loc_min
                                 && (c->climate_mode & 8) && !(c->climate_mode & 4));
        int big = (rw * rh) > 256;
        /* Si la condition a des enfants qui héritent de sa position (ex. height
         * sous un Locate W), l'early-exit pousserait le 1er point trouvé au lieu
         * du min/max EXACT (≠ cubiomes). On ne prend le raccourci que sans enfant. */
        int has_children = 0;
        if (env->co && env->co->ready && cond_idx >= 0 && cond_idx < KIDS_MAXN)
            has_children = (env->co->n_kids[cond_idx] > 0);
        if (big && !has_children &&
                   (single_min_upper || single_min_lower ||
                    single_max_lower || single_max_upper)) {
            /* Sous-échantillonnage adaptatif avec GARANTIE D'EXACTITUDE.
             * Le sous-échantillonnage seul pourrait rater un extremum entre deux
             * points (faux négatif/positif). Pour ne JAMAIS perdre d'efficacité :
             *  - mode "∃" (min_upper / max_lower) : on cherche un point décisif
             *    par sous-échantillonnage (rapide quand il existe, cas fréquent).
             *    Si AUCUN point sous-échantillonné ne décide, on retombe sur le
             *    balayage exact pour trancher → résultat identique au brute-force.
             *  - mode "∀" (min_lower / max_upper) : un sous-échantillon qui voit
             *    une violation rejette vite ; sinon balayage exact pour confirmer.
             * Ainsi la sortie est toujours exacte, et on gagne sur le cas commun. */
            int step_x = (rw > 64) ? (rw / 64) : 1;
            int step_z = (rh > 64) ? (rh / 64) : 1;
            const DoublePerlinNoise *dp = cdp;
            int found_x = rx, found_z = rz;
            int decided = 0, satisfied = 0;
            int exists_mode = (single_min_upper || single_max_lower);
            for (int dz = 0; dz < rh && !decided; dz += step_z) {
                for (int dx = 0; dx < rw; dx += step_x) {
                    double p = sampleDoublePerlinScattered(dp, rx+dx, 0, rz+dz) * 10000.0;
                    int point_ok;
                    if (single_min_upper)      point_ok = (p <= vmax);
                    else if (single_min_lower) point_ok = (p >= vmin);
                    else if (single_max_lower) point_ok = (p >= vmin);
                    else                       point_ok = (p <= vmax);
                    if (exists_mode) {
                        if (point_ok) { satisfied=1; decided=1; found_x=rx+dx; found_z=rz+dz; break; }
                    } else {
                        if (!point_ok) { satisfied=0; decided=1; found_x=rx+dx; found_z=rz+dz; break; }
                    }
                }
            }
            if (decided) {
                if (!satisfied) return 0;
                env_push(env, cond_idx, found_x << 2, found_z << 2);
                return 1;
            }
            /* Indécis par sous-échantillonnage → balayage EXACT pour trancher.
             * (Cas minoritaire : ~quelques % des seeds, donc coût amorti.) */
            TrackMM info2;
            info2.vmin = +INFINITY; info2.vmax = -INFINITY;
            info2.xmin = rx; info2.zmin = rz; info2.xmax = rx; info2.zmax = rz;
            double pl = +INFINITY, ph = -INFINITY;
            double *pmn = loc_min ? &pl : NULL;
            double *pmx = loc_max ? &ph : NULL;
            getParaRange(dp, pmn, pmx, rx, rz, rw, rh, &info2, f_track_mm);
            double em = loc_min ? info2.vmin : info2.vmax;
            double eM = loc_max ? info2.vmax : info2.vmin;
            if (!(em >= vmin && eM <= vmax)) return 0;
            int32_t ox2 = loc_min ? (info2.xmin << 2) : (info2.xmax << 2);
            int32_t oz2 = loc_min ? (info2.zmin << 2) : (info2.zmax << 2);
            env_push(env, cond_idx, ox2, oz2);
            return 1;
        }
        /* ── Cas général : balayage exact (petite zone ou test bilatéral) ── */
        TrackMM info;
        info.vmin = +INFINITY; info.vmax = -INFINITY;
        info.xmin = rx; info.zmin = rz; info.xmax = rx; info.zmax = rz;
        double p_lo = +INFINITY, p_hi = -INFINITY;
        double *p_min = loc_min ? &p_lo : NULL;
        double *p_max = loc_max ? &p_hi : NULL;
        getParaRange(cdp, p_min, p_max,
                      rx, rz, rw, rh, &info, f_track_mm);
        double evalmin = loc_min ? info.vmin : info.vmax;
        double evalmax = loc_max ? info.vmax : info.vmin;
        int pass;
        if (c->climate_invert) pass = !(evalmin > vmin && evalmax < vmax);
        else                   pass =  (evalmin >= vmin && evalmax <= vmax);
        if (!pass) return 0;
        int32_t ox, oz;
        if (c->climate_mode & 1) {
            ox = info.xmin << 2; oz = info.zmin << 2;
        } else {
            ox = info.xmax << 2; oz = info.zmax << 2;
        }
        env_push(env, cond_idx, ox, oz);
        return 1; }

    case CTYPE_CLIMATE_NOISE: {
        /* exactly cubiomes-viewer F_CLIMATE_NOISE: EXACT min / max of each
         * constrained parameter over the area (getParaRange; aborts as soon as
         * a value leaves the exclusion bounds), full octaves (CV reads the
         * fully seeded generator, whatever the octave setting). Before: a
         * sub-sampled grid (1 cell in N) → extremes missed → CV results lost. */
        static const int order[] = {0, 1, 5, 3, 2};
        int rx = x1 >> 2, rz = z1 >> 2;
        int rw = (x2 >> 2) - rx + 1; if (rw < 1) rw = 1;   /* viewer extent */
        int rh = (z2 >> 2) - rz + 1; if (rh < 1) rh = 1;
        const int INT_MIN_V = -2147483647 - 1;
        const int INT_MAX_V = 2147483647;
        for (int j = 0; j < 5; j++) {
            int i = order[j];
            if (c->cn_ok_lo[i] == INT_MIN_V && c->cn_ok_hi[i] == INT_MAX_V &&
                c->cn_ex_lo[i] == INT_MIN_V && c->cn_ex_hi[i] == INT_MAX_V)
                continue;
            const DoublePerlinNoise *ndp = clim_dp(env, i, -1);
            double pmin, pmax;
            Confine cf = { (double)c->cn_ex_lo[i], (double)c->cn_ex_hi[i] };
            if (getParaRange(ndp, &pmin, &pmax, rx, rz, rw, rh, &cf, f_confine))
                return 0;                       /* left the exclusion bounds */
            if (pmin > (double)c->cn_ok_hi[i] || pmax < (double)c->cn_ok_lo[i]) return 0;
            if (pmin < (double)c->cn_ex_lo[i] || pmax > (double)c->cn_ex_hi[i]) return 0;
        }
        env_push(env, cond_idx, cx_def, cz_def);
        return 1; }

    case CTYPE_HEIGHT: {
        if (!env->sn_ow_ready) {
            initSurfaceNoise(&env->sn_ow, DIM_OVERWORLD, env->seed);
            env->sn_ow_ready = 1;
        }
        env_set_dim(env, DIM_OVERWORLD);
        /* mapApproxHeight (MC 1.18+) exige un générateur de bruit COMPLÈTEMENT
         * initialisé (g.bn.nptype == -1). Une condition climat/noise antérieure
         * peut l'avoir laissé sur un para précis → mapApproxHeight retourne 1
         * sans calculer et y reste 0. On force applySeed si nécessaire. */
        if (env->g.bn.nptype != -1) {
            applySeed(&env->g, DIM_OVERWORLD, env->seed);
            env->current_dim = DIM_OVERWORLD;
            env->gbn_seed = ~0ULL;
            env->gbn_nmax = -999;
        }
        int32_t rx = cx_def >> 2, rz = cz_def >> 2;
        float y = 0.f;
        mapApproxHeight(&y, NULL, &env->g, &env->sn_ow, rx, rz, 1, 1);
        /* Sémantique IDENTIQUE à cubiomes (search.cpp F_HEIGHT) :
         *   in-range : ymin <= y <= ymax ; outside : y <= ymin || y >= ymax */
        if (c->height_invert) {
            if (!(y <= c->height_ymin || y >= c->height_ymax)) return 0;
        } else {
            if (!(y >= c->height_ymin && y <= c->height_ymax)) return 0;
        }
        env_push(env, cond_idx, cx_def, cz_def);
        return 1; }

    case CTYPE_CRUNCHINESS: {
        if (!test_crunchiness(env, c)) return 0;
        env_push(env, cond_idx, cx_def, cz_def);
        return 1; }

    default:
        return 0;
    }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * eval_node — evaluate condition + recurse into AND-children
 * ═══════════════════════════════════════════════════════════════════════════ */
static int eval_node_impl(SsaEnv *env, const SsaCond *conds, int n_conds,
                           CondCache *caches, int cond_1idx, int32_t at_x, int32_t at_z);

static int eval_node(SsaEnv *env, const SsaCond *conds, int n_conds,
                      CondCache *caches,
                      int cond_1idx, int32_t at_x, int32_t at_z)
{
    SsaProf *pf = env->prof;
    int i = cond_1idx - 1;
    if (!pf || i < 0 || i >= MAX_CONDS)
        return eval_node_impl(env, conds, n_conds, caches, cond_1idx, at_x, at_z);
    if (pf->reach_stamp[i] != pf->stamp) { pf->reach_stamp[i] = pf->stamp; pf->reached[i]++; }
    int r;
    if (pf->timing) {
        uint64_t saved = pf->child_ticks;
        pf->child_ticks = 0;
        uint64_t t0 = SSA_TICKS();
        r = eval_node_impl(env, conds, n_conds, caches, cond_1idx, at_x, at_z);
        uint64_t el = SSA_TICKS() - t0;
        pf->ticks[i] += el > pf->child_ticks ? el - pf->child_ticks : 0;
        pf->child_ticks = saved + el;
    } else {
        r = eval_node_impl(env, conds, n_conds, caches, cond_1idx, at_x, at_z);
    }
    if (r && pf->pass_stamp[i] != pf->stamp) { pf->pass_stamp[i] = pf->stamp; pf->passed[i]++; }
    return r;
}

static int eval_node_impl(SsaEnv *env, const SsaCond *conds, int n_conds,
                           CondCache *caches,
                           int cond_1idx, int32_t at_x, int32_t at_z)
{
    /* The Python build_ssacond_array() ensures cond_idx values are
     * contiguous 1..n, so cond_1idx-1 is a direct array index. */
    int idx0 = cond_1idx - 1;
    if (idx0 < 0 || idx0 >= n_conds) return 1;
    const SsaCond *c = &conds[idx0];
    /* Defensive: if the array isn't actually sequential (e.g. native SSA
     * conditions without explicit saves), fall back to linear search. */
    if (c->cond_idx != cond_1idx) {
        idx0 = -1;
        for (int i = 0; i < n_conds; i++) {
            if (conds[i].cond_idx == cond_1idx) { idx0 = i; break; }
        }
        if (idx0 < 0) return 1;
        c = &conds[idx0];
    }

    int parent_idx = c->parent_idx;

    /* Structure WITH dependent conditions — cubiomes-viewer semantics (BR_CLUST):
     *   instances == 1 : every instance is tried, the children must all pass
     *                    around ONE of them (OR over instances);
     *   instances  > 1 : the children are tested around the centroid of all the
     *                    instances found in the area.
     * (Before: only the first instance found was tried → missed seeds.) */
    if ((c->ctype == CTYPE_STRUCTURE_OW || c->ctype == CTYPE_STRUCTURE_NETHER
         || c->ctype == CTYPE_STRUCTURE_END) && !c->exclude) {
        const int *kids; int nk;
        int kids_fb[MAX_CONDS];
        if (env->co && env->co->ready
            && cond_1idx >= 0 && cond_1idx < KIDS_MAXN) {
            nk = env->co->n_kids[cond_1idx];
            kids = env->co->kids[cond_1idx];
        } else {
            int fn = 0;
            for (int i = 0; i < n_conds; i++)
                if (conds[i].cond_idx && conds[i].parent_idx == cond_1idx
                    && conds[i].cond_idx != cond_1idx)
                    kids_fb[fn++] = conds[i].cond_idx;
            kids = kids_fb; nk = fn;
        }
        if (nk > 0) {
            SsaPos sv = (parent_idx <= MAX_CONDS) ? env->positions[parent_idx]
                                                  : env->positions[0];
            if (parent_idx <= MAX_CONDS) env_push(env, parent_idx, at_x, at_z);
            int min_count = c->min_count <= 0 ? 1 : c->min_count;
            int res = 0;
            if (min_count == 1) {
                /* up to 4096 instances, as cubiomes-viewer (MAX_INSTANCES);
                 * before: 64 → on a large area only the first 64 villages were
                 * tried as anchors for the dependent conditions */
                Pos *inst = (Pos *)malloc(sizeof(Pos) * STRUCT_SPLIT_MAX);
                if (!inst) { if (parent_idx <= MAX_CONDS) env->positions[parent_idx] = sv; return 0; }
                int n = struct_collect(env, c, cond_1idx, caches, at_x, at_z,
                                       inst, STRUCT_SPLIT_MAX, STRUCT_SPLIT_MAX, NULL, NULL);
                prof_check(env, cond_1idx, n > 0);
                if (n > STRUCT_SPLIT_MAX) n = STRUCT_SPLIT_MAX;
                for (int k = 0; k < n && !res; k++) {
                    env_push(env, cond_1idx, inst[k].x, inst[k].z);
                    int ok = 1;
                    for (int i = 0; i < nk && ok; i++)
                        if (!eval_node(env, conds, n_conds, caches, kids[i],
                                       inst[k].x, inst[k].z))
                            ok = 0;
                    if (ok) res = 1;
                }
                free(inst);
            } else {
                Pos first; int64_t xs = 0, zs = 0;
                int n = struct_collect(env, c, cond_1idx, caches, at_x, at_z,
                                       &first, 1, 0, &xs, &zs);
                prof_check(env, cond_1idx, n >= min_count);
                if (n >= min_count) {
                    int32_t cx = (int32_t)(xs / n), cz = (int32_t)(zs / n);
                    env_push(env, cond_1idx, cx, cz);
                    res = 1;
                    for (int i = 0; i < nk && res; i++)
                        if (!eval_node(env, conds, n_conds, caches, kids[i], cx, cz))
                            res = 0;
                }
            }
            if (parent_idx <= MAX_CONDS) env->positions[parent_idx] = sv;
            return res;
        }
    }

    /* slime / mineshaft / biome centre / stronghold with dependent
     * conditions: cubiomes-viewer BR_CLUST semantics (see clust_node) */
    if (c->ctype == CTYPE_SLIME || c->ctype == CTYPE_MINESHAFT
        || c->ctype == CTYPE_BIOME_CENTER || c->ctype == CTYPE_STRONGHOLD) {
        const int *kids; int nk;
        int kids_fb[MAX_CONDS];
        if (env->co && env->co->ready
            && cond_1idx >= 0 && cond_1idx < KIDS_MAXN) {
            nk = env->co->n_kids[cond_1idx];
            kids = env->co->kids[cond_1idx];
        } else {
            int fn = 0;
            for (int i = 0; i < n_conds; i++)
                if (conds[i].cond_idx && conds[i].parent_idx == cond_1idx
                    && conds[i].cond_idx != cond_1idx)
                    kids_fb[fn++] = conds[i].cond_idx;
            kids = kids_fb; nk = fn;
        }
        if (nk > 0) {
            SsaPos sv = (parent_idx <= MAX_CONDS) ? env->positions[parent_idx]
                                                  : env->positions[0];
            if (parent_idx <= MAX_CONDS) env_push(env, parent_idx, at_x, at_z);
            int r = clust_node(env, conds, n_conds, caches, c, cond_1idx, at_x, at_z, kids, nk);
            if (parent_idx <= MAX_CONDS) env->positions[parent_idx] = sv;
            return r;
        }
    }

    /* Biome / noise sample with « Yield individual samples » and dependent
     * conditions — cubiomes-viewer BR_SPLIT with count == 1: each accepted
     * sample position is an instance; the children must all pass around ONE
     * of them (OR over the samples, in sampling order, up to 4096). Without
     * the option, the children are tested at the centre of the samples
     * (general path below, like cubiomes-viewer count != 1). */
    if ((c->ctype == CTYPE_BIOME_SAMPLE || c->ctype == CTYPE_NOISE_SAMPLE)
        && c->yield_samples) {
        const int *kids; int nk;
        int kids_fb[MAX_CONDS];
        if (env->co && env->co->ready
            && cond_1idx >= 0 && cond_1idx < KIDS_MAXN) {
            nk = env->co->n_kids[cond_1idx];
            kids = env->co->kids[cond_1idx];
        } else {
            int fn = 0;
            for (int i = 0; i < n_conds; i++)
                if (conds[i].cond_idx && conds[i].parent_idx == cond_1idx
                    && conds[i].cond_idx != cond_1idx)
                    kids_fb[fn++] = conds[i].cond_idx;
            kids = kids_fb; nk = fn;
        }
        if (nk > 0) {
            enum { YIELD_MAX = 4096 };      /* cubiomes-viewer MAX_INSTANCES */
            Pos *inst = (Pos *)malloc(sizeof(Pos) * YIELD_MAX);
            if (!inst) return 0;
            SsaPos sv = (parent_idx <= MAX_CONDS) ? env->positions[parent_idx]
                                                  : env->positions[0];
            if (parent_idx <= MAX_CONDS) env_push(env, parent_idx, at_x, at_z);
            env->sample_out = inst; env->sample_cap = YIELD_MAX; env->sample_n = 0;
            int r = eval_condition(env, c, cond_1idx, at_x, at_z, conds, n_conds, caches);
            prof_check(env, cond_1idx, r);
            env->sample_out = NULL;
            int n = env->sample_n;
            int res = 0;
            if (r) {
                for (int k = 0; k < n && !res; k++) {
                    env_push(env, cond_1idx, inst[k].x, inst[k].z);
                    int ok = 1;
                    for (int i = 0; i < nk && ok; i++)
                        if (!eval_node(env, conds, n_conds, caches, kids[i],
                                       inst[k].x, inst[k].z))
                            ok = 0;
                    if (ok) res = 1;
                }
            }
            free(inst);
            if (parent_idx <= MAX_CONDS) env->positions[parent_idx] = sv;
            return res;
        }
    }

    /* Court-circuit early : pour un nœud non-spiral et non-logique, on évalue
     * d'abord SA propre condition. Si elle échoue (cas fréquent — ex. la racine
     * climat rejette ~75% des graines), on retourne immédiatement sans collecter
     * ni trier les enfants. Les logiques (OR/NOT) gèrent leurs enfants
     * elles-mêmes dans eval_condition, donc on les laisse au chemin général. */
    /* CTYPE_STRONGHOLD est exclu du court-circuit : quand il a des enfants, on
     * itère l'anneau des strongholds et on évalue les enfants PAR stronghold
     * (linking joint — le village doit être près du MÊME stronghold), comme le
     * spiral. Il tombe donc dans le bloc général plus bas. */
    if (c->ctype != CTYPE_SPIRAL && c->ctype != CTYPE_LOGIC_OR
        && c->ctype != CTYPE_LOGIC_NOT && c->ctype != CTYPE_STRONGHOLD) {
        SsaPos sv = (parent_idx <= MAX_CONDS) ? env->positions[parent_idx]
                                              : env->positions[0];
        if (parent_idx <= MAX_CONDS) env_push(env, parent_idx, at_x, at_z);
        int r = eval_condition(env, c, cond_1idx, at_x, at_z,
                                conds, n_conds, caches);
        prof_check(env, cond_1idx, r);
        if (!r) {
            if (parent_idx <= MAX_CONDS) env->positions[parent_idx] = sv;
            return 0;
        }
        /* La condition passe : évaluer les enfants (AND). On itère directement
         * sur le tableau d'ordre précalculé (pas de copie locale). */
        const int *kids; int nk;
        int kids_fallback[MAX_CONDS];
        if (env->co && env->co->ready
            && cond_1idx >= 0 && cond_1idx < KIDS_MAXN) {
            nk = env->co->n_kids[cond_1idx];
            kids = env->co->kids[cond_1idx];
        } else {
            int fn = 0;
            for (int i = 0; i < n_conds; i++)
                if (conds[i].cond_idx && conds[i].parent_idx == cond_1idx
                    && conds[i].cond_idx != cond_1idx)
                    kids_fallback[fn++] = conds[i].cond_idx;
            kids = kids_fallback; nk = fn;
        }
        int res = 1;
        if (nk > 0) {
            int32_t child_x = at_x, child_z = at_z;
            if (cond_1idx < MAX_CONDS && env->positions[cond_1idx].valid) {
                child_x = env->positions[cond_1idx].x;
                child_z = env->positions[cond_1idx].z;
            }
            for (int i = 0; i < nk && res; i++)
                if (!eval_node(env, conds, n_conds, caches, kids[i],
                                child_x, child_z))
                    res = 0;
        }
        if (parent_idx <= MAX_CONDS) env->positions[parent_idx] = sv;
        return res;
    }

    int children[MAX_CONDS], n_children = 0;
    /* Ordre des enfants : utilise le cache précalculé si disponible (évite le
     * tri O(n²) récursif par graine), sinon collecte simple en fallback. */
    if (env->co && env->co->ready
        && cond_1idx >= 0 && cond_1idx < KIDS_MAXN) {
        n_children = env->co->n_kids[cond_1idx];
        for (int i = 0; i < n_children; i++)
            children[i] = env->co->kids[cond_1idx][i];
    } else {
        for (int i = 0; i < n_conds; i++) {
            if (conds[i].cond_idx && conds[i].parent_idx == cond_1idx
                && conds[i].cond_idx != cond_1idx)  /* anti-cycle */
                children[n_children++] = conds[i].cond_idx;
        }
        for (int a = 1; a < n_children; a++) {
            int key = children[a];
            int64_t kc = cond_subtree_cost(conds, n_conds, key);
            int b = a - 1;
            while (b >= 0 && cond_subtree_cost(conds, n_conds, children[b]) > kc) {
                children[b+1] = children[b]; b--;
            }
            children[b+1] = key;
        }
    }

    /* Fast-path : feuille non-spiral sans enfant (cas le plus fréquent — un
     * climat terminal). On évite la machinerie de récursion ; eval_condition
     * gère lui-même son env_push de position. */
    if (n_children == 0 && c->ctype != CTYPE_SPIRAL) {
        SsaPos sv = (parent_idx <= MAX_CONDS) ? env->positions[parent_idx]
                                              : env->positions[0];
        if (parent_idx <= MAX_CONDS) env_push(env, parent_idx, at_x, at_z);
        int r = eval_condition(env, c, cond_1idx, at_x, at_z,
                                conds, n_conds, caches);
        prof_check(env, cond_1idx, r);
        if (parent_idx <= MAX_CONDS) env->positions[parent_idx] = sv;
        return r;
    }

    SsaPos saved = (parent_idx <= MAX_CONDS) ? env->positions[parent_idx]
                                              : env->positions[0];
    env_push(env, parent_idx, at_x, at_z);
    int result = 0;

    if (c->ctype == CTYPE_SPIRAL) {
        int step = c->step > 0 ? c->step : 512;
        int32_t bx1, bz1, bx2, bz2; int64_t rmax_sq = 0;
        if (c->rmax > 0) {
            int32_t rb = c->rmax - 1;
            bx1 = at_x - rb; bz1 = at_z - rb;
            bx2 = at_x + rb; bz2 = at_z + rb;
            rmax_sq = (int64_t)rb*rb + 1;
        } else {
            bx1 = at_x + c->x1; bz1 = at_z + c->z1;
            bx2 = at_x + c->x2; bz2 = at_z + c->z2;
        }
        SpiralIter it;
        spiral_init(&it, bx1, bz1, bx2, bz2, step, rmax_sq, at_x, at_z);
        int32_t gx, gz;
        int spiral_guard = 0;
        while (spiral_next(&it, &gx, &gz)) {
            /* Arrêt réactif : une spirale fine sur une grande zone peut itérer
             * des dizaines de milliers de points ; on vérifie le drapeau Stop
             * périodiquement pour ne pas bloquer l'arrêt sur une seule seed. */
            if (env->stop_flag && ((++spiral_guard & 1023) == 0) && *env->stop_flag)
                break;
            env_push(env, cond_1idx, gx, gz);
            int all_ok = 1;
            for (int i = 0; i < n_children && all_ok; i++)
                if (!eval_node(env, conds, n_conds, caches, children[i], gx, gz))
                    all_ok = 0;
            if (all_ok) { result = 1; break; }
        }
    } else if (c->ctype == CTYPE_STRONGHOLD) {
        /* Anneau de strongholds comme ANCRE : on évalue les enfants PAR
         * stronghold de la zone, comme le spiral. Pass si UN stronghold a TOUS
         * ses enfants satisfaits (ex. « un village à côté de CE stronghold »).
         * On pousse la position de ce stronghold → les enfants s'y relient, et
         * le post-filtre Python (yeux) sait quel stronghold vérifier. */
        int max_ring = 0;
        if (stronghold_ring_check(env->mc, at_x, at_z, c, &max_ring)) {
            int32_t sx1, sz1, sx2, sz2; int64_t srmax = 0;
            if (c->rmax > 0) {
                int32_t rb = c->rmax - 1;
                sx1 = at_x - rb; sz1 = at_z - rb;
                sx2 = at_x + rb; sz2 = at_z + rb;
                srmax = (int64_t)rb*rb + 1;
            } else {
                sx1 = at_x + c->x1; sz1 = at_z + c->z1;
                sx2 = at_x + c->x2; sz2 = at_z + c->z2;
            }
            env_set_dim(env, DIM_OVERWORLD);
            StrongholdIter it;
            initFirstStronghold(&it, env->mc, env->seed);
            int sh_guard = 0;
            while (nextStronghold(&it, &env->g) > 0) {
                if (env->stop_flag && ((++sh_guard & 63) == 0) && *env->stop_flag)
                    break;
                int32_t spx = it.pos.x, spz = it.pos.z;
                int inside;
                if (srmax) {
                    int64_t dx = spx - at_x, dz = spz - at_z;
                    inside = (dx*dx + dz*dz < srmax);
                } else {
                    inside = (spx >= sx1 && spx <= sx2 && spz >= sz1 && spz <= sz2);
                }
                if (inside) {
                    env_push(env, cond_1idx, spx, spz);
                    int all_ok = 1;
                    for (int i = 0; i < n_children && all_ok; i++)
                        if (!eval_node(env, conds, n_conds, caches,
                                        children[i], spx, spz))
                            all_ok = 0;
                    if (all_ok) { result = 1; break; }
                }
                if (it.ringnum > max_ring) break;
            }
        }
    } else {
        result = eval_condition(env, c, cond_1idx, at_x, at_z,
                                 conds, n_conds, caches);
        if (result && c->ctype != CTYPE_LOGIC_OR && c->ctype != CTYPE_LOGIC_NOT) {
            int32_t child_x = at_x, child_z = at_z;
            if (cond_1idx < MAX_CONDS && env->positions[cond_1idx].valid) {
                child_x = env->positions[cond_1idx].x;
                child_z = env->positions[cond_1idx].z;
            }
            for (int i = 0; i < n_children && result; i++)
                if (!eval_node(env, conds, n_conds, caches, children[i],
                                child_x, child_z))
                    result = 0;
        }
    }

    if (parent_idx <= MAX_CONDS) env->positions[parent_idx] = saved;
    return result;
}


/* ═══════════════════════════════════════════════════════════════════════════
 * BR_CLUST conditions WITH dependent conditions (slime, mineshaft, biome
 * center, stronghold) — exactly cubiomes-viewer _testTreeAt / testCondAt:
 *   count == 1 : every instance of the area is an anchor, in CV's order and at
 *                CV's position (slime: chunk corner; mineshaft: chunk corner,
 *                x-major; biome center: the first centre only; stronghold:
 *                each stronghold) → the children must all pass around ONE (OR)
 *   count != 1 : the children are tested ONCE, around CV's average position
 *                (all instances of the area; exclusion (0): the area centre)
 * Before: the first instance (or the mean of the first `count`) was used and
 * the stronghold ignored count / exclusion → dependent conditions tested at
 * the wrong place. Returns -1 when the node type is not handled here.
 * ═══════════════════════════════════════════════════════════════════════════ */
static int clust_node(SsaEnv *env, const SsaCond *conds, int n_conds, CondCache *caches,
                      const SsaCond *c, int cond_1idx, int32_t at_x, int32_t at_z,
                      const int *kids, int nk)
{
    if (!(c->ctype == CTYPE_SLIME || c->ctype == CTYPE_MINESHAFT
          || c->ctype == CTYPE_BIOME_CENTER || c->ctype == CTYPE_STRONGHOLD))
        return -1;
    int32_t x1, z1, x2, z2; int64_t rmax_sq = 0;
    if (c->rmax > 0) {
        int32_t rb = c->rmax - 1;
        x1 = at_x - rb; z1 = at_z - rb; x2 = at_x + rb; z2 = at_z + rb;
        rmax_sq = (int64_t)rb*rb + 1;
    } else {
        x1 = at_x + c->x1; z1 = at_z + c->z1; x2 = at_x + c->x2; z2 = at_z + c->z2;
    }
    int count = c->exclude ? 0 : (c->min_count <= 0 ? 1 : c->min_count);
    enum { CLUST_MAX = 4096 };
    Pos *inst = (Pos *)malloc(sizeof(Pos) * CLUST_MAX);
    if (!inst) return 0;
    int n = 0;                          /* instances (branch mode: anchors) */
    int64_t xt = 0, zt = 0;             /* sums for the average mode */
    int avg_ok = 0; int32_t ax = 0, az = 0;
    int branch = (count == 1);

    if (c->ctype == CTYPE_SLIME) {
        int32_t cx1 = x1 >> 4, cz1 = z1 >> 4, cx2 = x2 >> 4, cz2 = z2 >> 4;
        for (int32_t rz = cz1; rz <= cz2; rz++)
            for (int32_t rx = cx1; rx <= cx2; rx++) {
                if (!is_slime_chunk(env->seed, rx, rz)) continue;
                if (c->skip_ref && rx == (at_x >> 4) && rz == (at_z >> 4)) continue;
                if (rmax_sq) {                  /* SSA radius option (CV: box only) */
                    int64_t dx = (rx << 4) + 8 - at_x, dz = (rz << 4) + 8 - at_z;
                    if (dx*dx + dz*dz >= rmax_sq) continue;
                }
                if (n < CLUST_MAX) { inst[n].x = rx << 4; inst[n].z = rz << 4; }
                xt += rx; zt += rz; n++;
            }
        if (count == 0) { avg_ok = (n == 0); ax = (x1 + x2) >> 1; az = (z1 + z2) >> 1; }
        else if (n >= count && n > 0) { avg_ok = 1; ax = (int32_t)((xt << 4) / n) + 8; az = (int32_t)((zt << 4) / n) + 8; }
    } else if (c->ctype == CTYPE_MINESHAFT) {
        int m = getMineshafts(env->mc, env->seed, x1 >> 4, z1 >> 4, x2 >> 4, z2 >> 4, inst, CLUST_MAX);
        if (m > CLUST_MAX) m = CLUST_MAX;
        for (int i = 0; i < m; i++) {
            if (rmax_sq) {
                int64_t dx = inst[i].x - at_x, dz = inst[i].z - at_z;
                if (dx*dx + dz*dz >= rmax_sq) continue;
            }
            if (c->skip_ref && inst[i].x == at_x && inst[i].z == at_z) continue;
            inst[n++] = inst[i]; xt += inst[i].x; zt += inst[i].z;
        }
        if (count == 0) { avg_ok = (n == 0); ax = (x1 + x2) >> 1; az = (z1 + z2) >> 1; }
        else if (n >= count && n > 0) { avg_ok = 1; ax = (int32_t)(xt / n); az = (int32_t)(zt / n); }
    } else if (c->ctype == CTYPE_BIOME_CENTER) {
        env_set_dim(env, DIM_OVERWORLD);
        Range r;
        r.scale = 4; r.x = x1 >> 2; r.z = z1 >> 2;
        r.sx = (x2 >> 2) - r.x + 1; if (r.sx < 1) r.sx = 1;
        r.sz = (z2 >> 2) - r.z + 1; if (r.sz < 1) r.sz = 1;
        r.y = c->biome_y >> 2; r.sy = 1;
        /* CV: branch mode asks for `count` (= 1) centres; the average mode
         * for all of them; exclusion for 1 */
        int want = branch ? 1 : (count == 0 ? 1 : CLUST_MAX);
        int m = getBiomeCenters(inst, NULL, want, &env->g, r, c->biome_id,
                                c->biome_min_size, c->biome_tolerance, NULL);
        if (m > want) m = want;
        if (count == 0) {
            avg_ok = (m == 0); ax = (x1 + x2) >> 1; az = (z1 + z2) >> 1; n = 0;
        } else {
            for (int i = 0; i < m; i++) {
                if (c->skip_ref && inst[i].x == at_x && inst[i].z == at_z) continue;
                if (rmax_sq) {                  /* SSA radius option */
                    int64_t dx = inst[i].x - at_x, dz = inst[i].z - at_z;
                    if (dx*dx + dz*dz >= rmax_sq) continue;
                }
                inst[n++] = inst[i]; xt += inst[i].x; zt += inst[i].z;
            }
            if (n >= count && n > 0) { avg_ok = 1; ax = (int32_t)(xt / n) + 2; az = (int32_t)(zt / n) + 2; }
        }
    } else {                                    /* CTYPE_STRONGHOLD */
        int max_ring = 0;
        ax = (x1 + x2) >> 1; az = (z1 + z2) >> 1;
        if (!stronghold_ring_check(env->mc, at_x, at_z, c, &max_ring)) {
            avg_ok = (count == 0); n = 0;
        } else {
            env_set_dim(env, DIM_OVERWORLD);
            StrongholdIter it;
            initFirstStronghold(&it, env->mc, env->seed);
            int excluded = 0;
            while (nextStronghold(&it, &env->g) > 0) {
                if (env->stop_flag && *env->stop_flag) break;
                int inside;
                if (rmax_sq) {
                    int64_t dx = it.pos.x - at_x, dz = it.pos.z - at_z;
                    inside = (dx*dx + dz*dz < rmax_sq);
                } else {
                    inside = (it.pos.x >= x1 && it.pos.x <= x2 && it.pos.z >= z1 && it.pos.z <= z2);
                }
                if (c->skip_ref && it.pos.x == at_x && it.pos.z == at_z) inside = 0;
                if (inside) {
                    if (count == 0) { excluded = 1; break; }
                    if (n < CLUST_MAX) inst[n] = it.pos;
                    xt += it.pos.x; zt += it.pos.z; n++;
                }
                if (it.ringnum > max_ring) break;
            }
            if (count == 0) avg_ok = !excluded;
            else if (n >= count && n > 0) { avg_ok = 1; ax = (int32_t)(xt / n); az = (int32_t)(zt / n); }
        }
    }

    prof_check(env, cond_1idx, branch ? n > 0 : avg_ok);
    int res = 0;
    if (branch) {
        if (n > CLUST_MAX) n = CLUST_MAX;
        for (int k = 0; k < n && !res; k++) {
            env_push(env, cond_1idx, inst[k].x, inst[k].z);
            int ok = 1;
            for (int i = 0; i < nk && ok; i++)
                if (!eval_node(env, conds, n_conds, caches, kids[i], inst[k].x, inst[k].z))
                    ok = 0;
            if (ok) res = 1;
        }
    } else if (avg_ok) {
        env_push(env, cond_1idx, ax, az);
        res = 1;
        for (int i = 0; i < nk && res; i++)
            if (!eval_node(env, conds, n_conds, caches, kids[i], ax, az))
                res = 0;
    }
    free(inst);
    return res;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Fast 48-bit-only position check for structure conditions.
 * Returns 1 if at least one valid structure position exists in the zone.
 * Skips biome/terrain validation (those are deferred to the full pass).
 * ═══════════════════════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════════════════════
 * CRUNCHINESS — fast-path y_frac extraction
 *
 * This matches cubiomes' setClimateParaSeed + xPerlinInit bit-for-bit, but
 * skips the 256-entry permutation shuffle and the c (z-offset) read since
 * we only need y_frac = frac(b * 256) where b = xNextDouble().
 *
 * Saves ~95% of the time per seed vs the slow path (cubiomes initBiomeNoise
 * + setClimateParaSeed).
 * ═══════════════════════════════════════════════════════════════════════════ */

/* MD5 hashes for the 5 climate parameters.
 * md5_climate[climate][variant][0..1] where variant=0 is normal, variant=1
 * is large biomes. Weirdness has no "_large" variant, so both slots are the
 * same hash. */
static const uint64_t md5_climate[6][2][2] = {
    /* NP_TEMPERATURE */
    {{0x5c7e6b29735f0d7fULL, 0xf7d86f1bbc734988ULL},
     {0x944b0073edf549dbULL, 0x4ff44347e9d22b96ULL}},
    /* NP_HUMIDITY (vegetation) */
    {{0x81bb4d22e8dc168eULL, 0xf1c8b4bea16303cdULL},
     {0x71b8ab943dbd5301ULL, 0xbb63ddcf39ff7a2bULL}},
    /* NP_CONTINENTALNESS */
    {{0x83886c9d0ae3a662ULL, 0xafa638a61b42e8adULL},
     {0x9a3f51a113fce8dcULL, 0xee2dbd157e5dcdadULL}},
    /* NP_EROSION */
    {{0xd02491e6058f6fd8ULL, 0x4792512c94c17a80ULL},
     {0x8c984b1f8702a951ULL, 0xead7b1f92bae535fULL}},
    /* NP_SHIFT (unused) */
    {{0,0},{0,0}},
    /* NP_WEIRDNESS (ridge) — no _large variant in MC; same hash */
    {{0xefc8ef4d36102b34ULL, 0x1beeeb324a0f24eaULL},
     {0xefc8ef4d36102b34ULL, 0x1beeeb324a0f24eaULL}},
};

/* Per-octave MD5 hashes for octave_-12 .. octave_0 (13 entries).
 * Index = first_octave + 12 + i. */
static const uint64_t md5_octave_n[13][2] = {
    {0xb198de63a8012672ULL, 0x7b84cad43ef7b5a8ULL}, /* -12 */
    {0x0fd787bfbc403ec3ULL, 0x74a4a31ca21b48b8ULL}, /* -11 */
    {0x36d326eed40efeb2ULL, 0x5be9ce18223c636aULL}, /* -10 */
    {0x082fe255f8be6631ULL, 0x4e96119e22dedc81ULL}, /*  -9 */
    {0x0ef68ec68504005eULL, 0x48b6bf93a2789640ULL}, /*  -8 */
    {0xf11268128982754fULL, 0x257a1d670430b0aaULL}, /*  -7 */
    {0xe51c98ce7d1de664ULL, 0x5f9478a733040c45ULL}, /*  -6 */
    {0x6d7b49e7e429850aULL, 0x2e3063c622a24777ULL}, /*  -5 */
    {0xbd90d5377ba1b762ULL, 0xc07317d419a7548dULL}, /*  -4 */
    {0x53d39c6752dac858ULL, 0xbcd1c5a80ab65b3eULL}, /*  -3 */
    {0xb4a24d7a84e7677bULL, 0x023ff9668e89b5c4ULL}, /*  -2 */
    {0xdffa22b534c5f608ULL, 0xb9b67517d3665ca9ULL}, /*  -1 */
    {0xd50708086cef4d7cULL, 0x6e1651ecc7f43309ULL}, /*   0 */
};

/* Climate noise parameters. Amplitudes are the same in normal and large
 * biomes; only the first_octave and the hash change. */
typedef struct {
    int first_octave_normal;
    int first_octave_large;
    int n_amps;
    double amplitudes[10];
} CrunchClimateInfo;

static const CrunchClimateInfo crunch_climate_info[6] = {
    /* NP_TEMPERATURE */
    {-10, -12, 6, {1.5, 0, 1, 0, 0, 0, 0, 0, 0, 0}},
    /* NP_HUMIDITY (vegetation) */
    {-8,  -10, 6, {1, 1, 0, 0, 0, 0, 0, 0, 0, 0}},
    /* NP_CONTINENTALNESS */
    {-9,  -11, 9, {1, 1, 2, 2, 2, 1, 1, 1, 1, 0}},
    /* NP_EROSION */
    {-9,  -11, 5, {1, 1, 0, 1, 1, 0, 0, 0, 0, 0}},
    /* NP_SHIFT (unused) */
    {0,   0,   0, {0}},
    /* NP_WEIRDNESS (ridge) — same first_octave in both modes */
    {-7,  -7,  6, {1, 2, 1, 0, 0, 0, 0, 0, 0, 0}},
};

/* Compute y_frac for one octave starting from the per-stream root (xlo, xhi)
 * that has already been advanced past the per-stream "header" (the 2
 * xNextLong at the top of xOctaveInit).
 *
 * Following cubiomes xPerlinInit (only the parts we need):
 *   1. XOR root with md5_octave_n[hidx] → fresh Xoroshiro
 *   2. consume xNextDouble (= xNextLong) for `a`  (we discard)
 *   3. consume xNextLong for `b`, then b = (b >> 11) * 2^-53 * 256
 *      then y_frac = b - floor(b)
 *
 * The other RNG consumption inside xPerlinInit (c=xNextDouble + 256
 * xNextInt for the shuffle) does NOT affect this octave's y_frac and is
 * not used by subsequent octaves (each octave uses its own xor of the
 * xlo/xhi from xOctaveInit), so we can skip it.
 */
static inline float crunch_yfrac_for_octave(uint64_t root_lo, uint64_t root_hi,
                                              int hidx)
{
    Xoroshiro xr;
    xr.lo = root_lo ^ md5_octave_n[hidx][0];
    xr.hi = root_hi ^ md5_octave_n[hidx][1];
    /* skip "a" */
    xNextLong(&xr);
    /* read "b" */
    uint64_t b_raw = xNextLong(&xr);
    /* b = nextDouble * 256 = (b_raw >> 11) * 2^-53 * 256 = (b_raw >> 11) * 2^-45
     * y_frac = b - floor(b) = ((b_raw >> 11) & ((1<<45)-1)) * 2^-45
     */
    uint64_t bits45 = (b_raw >> 11) & ((1ULL << 45) - 1);
    return (float)((double)bits45 * (1.0 / (double)(1ULL << 45)));
}

/* Compute the per-octave root (xlo, xhi) for either A or B stream of a
 * given climate, after the xNextLong x2 consumption at the start of each
 * xOctaveInit call. */
static inline void crunch_stream_roots(uint64_t seed, int climate, int large,
                                        uint64_t *a_lo, uint64_t *a_hi,
                                        uint64_t *b_lo, uint64_t *b_hi)
{
    Xoroshiro pxr;
    xSetSeed(&pxr, seed);
    uint64_t xlo = xNextLong(&pxr);
    uint64_t xhi = xNextLong(&pxr);
    int v = large ? 1 : 0;
    /* noise_random = (xlo ^ climate_lo, xhi ^ climate_hi) */
    pxr.lo = xlo ^ md5_climate[climate][v][0];
    pxr.hi = xhi ^ md5_climate[climate][v][1];
    /* xOctaveInit for A: consume 2 xNextLong as its xlo/xhi */
    *a_lo = xNextLong(&pxr);
    *a_hi = xNextLong(&pxr);
    /* xOctaveInit for B: consume 2 more */
    *b_lo = xNextLong(&pxr);
    *b_hi = xNextLong(&pxr);
}

/* Compute crunchiness score for one seed. Score = sum over selected
 * octaves of weight[k] * |y_frac_k - 0.5|. */
static float compute_crunchiness_score(uint64_t seed, int climate, int large,
                                         uint32_t octave_mask,
                                         const float *weights)
{
    if (climate < 0 || climate >= 6 || climate == 4) return 0.0f;
    const CrunchClimateInfo *info = &crunch_climate_info[climate];
    if (info->n_amps <= 0) return 0.0f;

    uint64_t a_lo, a_hi, b_lo, b_hi;
    crunch_stream_roots(seed, climate, large, &a_lo, &a_hi, &b_lo, &b_hi);

    float score = 0.0f;
    int first_octave = large ? info->first_octave_large : info->first_octave_normal;
    int hash_base = first_octave + 12;
    /* Iterate amplitude slots. An octave with amp==0 is skipped (no slot
     * in the resulting octave array). We need to remap "active octave i"
     * (0..n_active-1) to amplitude slot index (0..n_amps-1). */
    int active_idx = 0;
    for (int amp_i = 0; amp_i < info->n_amps; amp_i++) {
        if (info->amplitudes[amp_i] == 0.0) continue;
        int hidx = hash_base + amp_i;
        if (hidx < 0 || hidx >= 13) { active_idx++; continue; }
        if (active_idx >= 6) break;  /* user can only select up to 6 octave pairs */
        /* A stream */
        if (octave_mask & (1u << active_idx)) {
            float y = crunch_yfrac_for_octave(a_lo, a_hi, hidx);
            float dev = y - 0.5f; if (dev < 0) dev = -dev;
            score += weights[2*active_idx] * dev;
        }
        /* B stream */
        if (octave_mask & (1u << (16 + active_idx))) {
            float y = crunch_yfrac_for_octave(b_lo, b_hi, hidx);
            float dev = y - 0.5f; if (dev < 0) dev = -dev;
            score += weights[2*active_idx + 1] * dev;
        }
        active_idx++;
    }
    return score;
}

/* Evaluate a crunchiness condition: returns 1 if it passes, 0 if it fails. */
static int test_crunchiness(SsaEnv *env, const SsaCond *c)
{
    float score = compute_crunchiness_score(
        env->seed, c->crunch_climate, env->large,
        c->crunch_octave_mask, c->crunch_weights);
    if (c->crunch_mode == 0) {
        return score <= c->crunch_threshold ? 1 : 0;
    } else {
        return score >= c->crunch_threshold ? 1 : 0;
    }
}

/* Public: compute y_frac of a single octave (active_idx among non-zero
 * amplitudes). Returns -1.0 on bad args. Used by gui.py for the heatmap. */
EXPORT float ssa_crunch_yfrac(int64_t seed, int climate, int octave_pair,
                                int is_b_stream, int large)
{
    if (climate < 0 || climate >= 6 || climate == 4) return -1.0f;
    const CrunchClimateInfo *info = &crunch_climate_info[climate];
    if (octave_pair < 0) return -1.0f;
    int active_idx = 0, amp_slot = -1;
    for (int i = 0; i < info->n_amps; i++) {
        if (info->amplitudes[i] == 0.0) continue;
        if (active_idx == octave_pair) { amp_slot = i; break; }
        active_idx++;
    }
    if (amp_slot < 0) return -1.0f;
    int first_octave = large ? info->first_octave_large : info->first_octave_normal;
    int hidx = first_octave + 12 + amp_slot;
    if (hidx < 0 || hidx >= 13) return -1.0f;

    uint64_t a_lo, a_hi, b_lo, b_hi;
    crunch_stream_roots((uint64_t)seed, climate, large, &a_lo, &a_hi, &b_lo, &b_hi);
    if (is_b_stream)
        return crunch_yfrac_for_octave(b_lo, b_hi, hidx);
    else
        return crunch_yfrac_for_octave(a_lo, a_hi, hidx);
}

/* p96: everything the GPU crunchiness pre-filter needs to compute EXACTLY the
 * score of compute_crunchiness_score(): the climate hash (normal / large) and,
 * for each active octave pair i in order, its octave hash and the A / B
 * weights (0 when the pair / stream is not selected in the mask). Before, the
 * kernel used fixed Commission weights on octaves first..first+2 (ignoring
 * zero amplitudes, Large Biomes and the user's weights): not a superset of the
 * CPU test -> GPU searches missed most results. Returns the number of pairs. */
EXPORT int ssa_crunch_gpu_params(int climate, int large, uint32_t octave_mask,
                                 const float *weights, uint64_t *clim_hash,
                                 uint64_t *oct_lo, uint64_t *oct_hi,
                                 float *w_a, float *w_b, int max_pairs)
{
    if (climate < 0 || climate >= 6 || climate == 4 || max_pairs <= 0) return 0;
    const CrunchClimateInfo *info = &crunch_climate_info[climate];
    int v = large ? 1 : 0;
    clim_hash[0] = md5_climate[climate][v][0];
    clim_hash[1] = md5_climate[climate][v][1];
    int first_octave = large ? info->first_octave_large : info->first_octave_normal;
    int hash_base = first_octave + 12;
    int n = 0, active_idx = 0;
    for (int amp_i = 0; amp_i < info->n_amps; amp_i++) {
        if (info->amplitudes[amp_i] == 0.0) continue;
        int hidx = hash_base + amp_i;
        if (hidx < 0 || hidx >= 13) { active_idx++; continue; }
        if (active_idx >= 6 || n >= max_pairs) break;
        oct_lo[n] = md5_octave_n[hidx][0];
        oct_hi[n] = md5_octave_n[hidx][1];
        w_a[n] = (octave_mask & (1u << active_idx)) ? weights[2*active_idx] : 0.0f;
        w_b[n] = (octave_mask & (1u << (16 + active_idx))) ? weights[2*active_idx + 1] : 0.0f;
        n++;
        active_idx++;
    }
    return n;
}

/* Returns the number of active (non-zero-amplitude) octave pairs for the
 * given climate, for the heatmap UI. */
EXPORT int ssa_crunch_n_octaves(int climate)
{
    if (climate < 0 || climate >= 6 || climate == 4) return 0;
    const CrunchClimateInfo *info = &crunch_climate_info[climate];
    int n = 0;
    for (int i = 0; i < info->n_amps; i++)
        if (info->amplitudes[i] != 0.0) n++;
    if (n > 6) n = 6;  /* UI cap */
    return n;
}

/* Debug: compute the same y_frac using cubiomes' slow path (setClimateParaSeed
 * + read .d2 from the resulting PerlinNoise). Used in tests to validate our
 * fast path. Returns -1.0 on failure. */
EXPORT double ssa_crunch_yfrac_slow(int64_t seed, int climate,
                                      int octave_pair, int is_b_stream, int large)
{
    if (climate < 0 || climate >= 6 || climate == 4) return -1.0;
    BiomeNoise bn;
    memset(&bn, 0, sizeof(bn));
    initBiomeNoise(&bn, MC_NEWEST);
    setClimateParaSeed(&bn, (uint64_t)seed, large ? 1 : 0, climate, -1);
    const DoublePerlinNoise *dpn = &bn.climate[climate];
    const OctaveNoise *on = is_b_stream ? &dpn->octB : &dpn->octA;
    if (octave_pair < 0 || octave_pair >= on->octcnt) return -1.0;
    return on->octaves[octave_pair].d2;
}


static int test_structure_pos_only(SsaEnv *env, const SsaCond *c,
                                     CondCache *cache,
                                     int32_t at_x, int32_t at_z)
{
    int32_t x1, z1, x2, z2; int64_t rmax_sq;
    if (c->rmax > 0) {
        int32_t rb = c->rmax - 1;
        x1 = at_x - rb; z1 = at_z - rb;
        x2 = at_x + rb; z2 = at_z + rb;
        rmax_sq = (int64_t)rb*rb + 1;
    } else {
        x1 = at_x + c->x1; z1 = at_z + c->z1;
        x2 = at_x + c->x2; z2 = at_z + c->z2;
        rmax_sq = 0;
    }
    int stype = c->struct_type;
    if (!cache->sconf_ready) {
        if (!getStructureConfig(stype, env->mc, &cache->sconf)) return 0;
        cache->sconf_ready = 1;
    }
    int region_size = cache->sconf.regionSize;
    if (region_size <= 0) return 0;

    int32_t rx1, rz1, rx2, rz2;
    if (region_size == 32) {
        rx1 = x1 >> 9; rz1 = z1 >> 9;
        rx2 = x2 >> 9; rz2 = z2 >> 9;
    } else if (region_size == 1) {
        rx1 = x1 >> 4; rz1 = z1 >> 4;
        rx2 = x2 >> 4; rz2 = z2 >> 4;
    } else {
        int csz = region_size << 4;
        rx1 = (x1 >= 0) ? (x1 / csz) : -((-x1 + csz - 1) / csz);
        rz1 = (z1 >= 0) ? (z1 / csz) : -((-z1 + csz - 1) / csz);
        rx2 = (x2 >= 0) ? (x2 / csz) : -((-x2 + csz - 1) / csz);
        rz2 = (z2 >= 0) ? (z2 / csz) : -((-z2 + csz - 1) / csz);
    }

    int min_count = c->min_count <= 0 ? 1 : c->min_count;
    int exclude = c->exclude;
    /* An EXCLUSION cannot be decided from positions alone: a candidate
     * position in the area only excludes the seed if the structure really
     * generates there (biome / terrain — 64-bit). Before: any candidate
     * rejected the whole 48-bit family (Blocks) or the seed (Incremental
     * pre-pass) → results lost for « no village here » sessions. */
    if (exclude) return 1;
    int hits = 0;
    for (int32_t rz = rz1; rz <= rz2; rz++) {
        for (int32_t rx = rx1; rx <= rx2; rx++) {
            Pos p;
            if (!getStructurePos(stype, env->mc, env->seed, rx, rz, &p))
                continue;
            int inside;
            if (rmax_sq) {
                int64_t dx = p.x - at_x, dz = p.z - at_z;
                inside = (dx*dx + dz*dz < rmax_sq);
            } else {
                inside = (p.x >= x1 && p.x <= x2 && p.z >= z1 && p.z <= z2);
            }
            if (!inside) continue;
            hits++;
            if (hits >= min_count) return exclude ? 0 : 1;
        }
    }
    return exclude ? 1 : 0;
}

/* Returns 1 if all top-level structure-position checks pass without doing
 * the full biome/terrain validation. Used in the 2-pass fast-skip mode.
 * Conditions that are NOT structure positions are conservatively considered
 * "maybe ok" (return 1) — they'll be re-checked in the full pass.
 */
static int ssa_test_seed_pos_only(SsaEnv *env, const SsaCond *conds,
                                    int n_conds, CondCache *caches)
{
    for (int i = 0; i < n_conds; i++) {
        if (conds[i].parent_idx != 0 || conds[i].cond_idx <= 0) continue;
        int ctype = conds[i].ctype;
        if (ctype == CTYPE_STRUCTURE_OW ||
            ctype == CTYPE_STRUCTURE_NETHER ||
            ctype == CTYPE_STRUCTURE_END) {
            /* For nether/end structures we'd need to switch dim — but
             * getStructurePos doesn't care about dim, it's pure RNG arithmetic.
             * The biome check is what needs the dim, so we can skip it here. */
            if (!test_structure_pos_only(env, &conds[i],
                                          &caches[conds[i].cond_idx - 1],
                                          0, 0))
                return 0;
        }
        /* Other condition types: conservatively "maybe ok" → no skip */
    }
    return 1;
}

/* Returns 1 if all top-level conditions are "structure-position" conditions
 * (i.e. eligible for the 48-bit fast-skip optimisation). */
static int conditions_eligible_for_2pass(const SsaCond *conds, int n_conds)
{
    int n_structure = 0;
    for (int i = 0; i < n_conds; i++) {
        if (conds[i].parent_idx != 0 || conds[i].cond_idx <= 0) continue;
        int ctype = conds[i].ctype;
        if (ctype == CTYPE_STRUCTURE_OW ||
            ctype == CTYPE_STRUCTURE_NETHER ||
            ctype == CTYPE_STRUCTURE_END) {
            n_structure++;
        } else {
            return 0;  /* non-structure top-level → can't 2-pass */
        }
    }
    return n_structure > 0;
}

/* ── Classification pour l'auto-adaptation du mode 48-bit (façon cubiomes) ──
 * Une condition est « dep48 » si sa POSITION/existence ne dépend que des 48
 * bits bas de la seed (positions de structures, slime) → testable dans la
 * passe 48-bit. Elle est « dep64 » si elle dépend des 64 bits (biomes,
 * climats, hauteur, spawn, stronghold…) → ne peut être validée qu'avec la
 * seed complète.
 *
 * cubiomes filtre d'abord sur les conditions dep48 (48 bits), puis n'étend les
 * 65536 bits hauts QUE pour vérifier les conditions dep64 sur les survivantes.
 * On reproduit ça : si la session contient ≥1 structure dep48 ET ≥1 condition
 * dep64, le mode 48-bit s'exécute en stratégie BLOCKS (préfiltre + expand).
 * Si elle ne contient QUE des structures dep48 → FAM48 pur (1 rep/famille).
 * Si elle ne contient AUCUNE structure dep48 → INC (balayage incrémental). */
static int ctype_is_dep48(int ctype) {
    /* Conditions dont le filtre ne dépend que des 48 bits bas. */
    return (ctype == CTYPE_STRUCTURE_OW ||
            ctype == CTYPE_STRUCTURE_NETHER ||
            ctype == CTYPE_STRUCTURE_END ||
            ctype == CTYPE_SLIME);
}

/* Compte les conditions top-level dep48 (structures/slime) et dep64 (le reste).
 * Les nœuds logiques (OR/NOT) et scale_to_* sont neutres : on regarde leur
 * sous-arbre pour savoir s'ils contiennent du dep48 « pur ». Par prudence,
 * un OR/NOT en top-level est considéré comme potentiellement dep64 (on ne
 * peut pas garantir que toutes ses branches sont dep48), ce qui forcera la
 * stratégie BLOCKS — correcte dans tous les cas. */
static void classify_session(const SsaCond *conds, int n_conds,
                              int *out_n_dep48, int *out_n_dep64) {
    int n48 = 0, n64 = 0;
    for (int i = 0; i < n_conds; i++) {
        if (conds[i].parent_idx != 0 || conds[i].cond_idx <= 0) continue;
        int ct = conds[i].ctype;
        if (ctype_is_dep48(ct)) {
            n48++;
        } else if (ct == CTYPE_LOGIC_OR || ct == CTYPE_LOGIC_NOT) {
            /* Un logique top-level : on inspecte ses descendants. S'il ne
             * contient QUE du dep48, il reste dep48 ; sinon dep64. */
            int sub64 = 0, sub48 = 0;
            for (int j = 0; j < n_conds; j++) {
                if (conds[j].cond_idx <= 0) continue;
                /* descendant direct ou indirect ? approximation : on teste
                 * tous les nœuds dont la chaîne de parents mène à i+1. */
                int p = conds[j].parent_idx, guard = 0;
                int is_desc = 0;
                while (p > 0 && guard++ < MAX_CONDS) {
                    if (p == conds[i].cond_idx) { is_desc = 1; break; }
                    /* remonter */
                    int pj = -1;
                    for (int k = 0; k < n_conds; k++)
                        if (conds[k].cond_idx == p) { pj = k; break; }
                    if (pj < 0) break;
                    p = conds[pj].parent_idx;
                }
                if (is_desc) {
                    if (ctype_is_dep48(conds[j].ctype)) sub48++;
                    else if (conds[j].ctype != CTYPE_LOGIC_OR &&
                             conds[j].ctype != CTYPE_LOGIC_NOT) sub64++;
                }
            }
            if (sub64 > 0 || sub48 == 0) n64++; else n48++;
        } else {
            n64++;
        }
    }
    *out_n_dep48 = n48;
    *out_n_dep64 = n64;
}

/* Heuristic cost per condition type. Used to evaluate cheap conditions first
 * so that expensive ones (Monte Carlo, spawn, stronghold) only run on seeds
 * that have already passed the cheap filters. Numbers are relative — only
 * the ordering matters. They were calibrated from per-cond benchmarks. */
static int64_t spiral_positions(const SsaCond *c) {
    int64_t step = c->step > 0 ? c->step : 512;
    int64_t w, h;
    if (c->rmax > 0) { w = h = 2 * (int64_t)(c->rmax - 1); }
    else { w = (int64_t)c->x2 - c->x1; h = (int64_t)c->z2 - c->z1; }
    if (w < 0) w = -w;
    if (h < 0) h = -h;
    return (w / step + 1) * (h / step + 1);
}

/* number of cells an area condition scans at `scale` blocks per cell */
static double area_cells(const SsaCond *c, int scale) {
    double w, h;
    if (c->rmax > 0) { w = h = 2.0 * (c->rmax - 1); }
    else { w = fabs((double)c->x2 - c->x1); h = fabs((double)c->z2 - c->z1); }
    if (scale < 1) scale = 1;
    return (w / scale + 1) * (h / scale + 1);
}

/* Estimated cost of ONE evaluation, in MICROSECONDS (measured on the
 * condition types: point climate 1-18 µs by octaves, a 256-block climate
 * box ~400 µs, a noise sample 10-30 µs, getSpawn ~ms…). Used to evaluate
 * cheap conditions first so that expensive ones only run on seeds that
 * passed the cheap filters; only the ORDER matters, but it must hold
 * across types — hence real units, and the area / octaves taken into
 * account instead of one number per type. */
static int cond_cost_heuristic(const SsaCond *c) {
    double v;
    switch (c->ctype) {
        case CTYPE_SLIME:           v = 1; break;
        case CTYPE_LOGIC_OR:        v = 0.2; break;
        case CTYPE_LOGIC_NOT:       v = 0.2; break;
        case CTYPE_SCALE_TO_NETHER: v = 0.2; break;
        case CTYPE_SCALE_TO_OVERWORLD: v = 0.2; break;
        case CTYPE_CRUNCHINESS:     v = 3; break;
        case CTYPE_CLIMATE_MINMAX: {
            /* ~1 µs per octave pair; full octaves (nmax <= 0) ~18 µs; an area
             * costs about sqrt(cells) points (range bounds prune the rest) */
            int n = c->climate_nmax;
            double pt = (n == 1) ? 1 : (n == 2) ? 2 : (n > 0 && n <= 4) ? 4
                      : (n > 0 && n <= 8) ? 8 : 18;
            double cells = area_cells(c, 4);
            v = pt * (1.0 + (cells > 1 ? sqrt(cells) / 3.0 : 0));
            break;
        }
        case CTYPE_CLIMATE_NOISE:   v = 6; break;
        case CTYPE_CLIMATE_AREA: {  /* the start cell rejects most seeds; a pass
                                       samples ~need cells (all with children) */
            int n = c->climate_nmax;
            double pt = (n == 1) ? 1 : (n == 2) ? 2 : (n > 0 && n <= 4) ? 4
                      : (n > 0 && n <= 8) ? 8 : 18;
            double need = c->biome_min_size > 0 ? c->biome_min_size : 1;
            v = pt * (1.0 + 0.3 * need);
            break;
        }
        case CTYPE_NOISE_SAMPLE: {  /* Monte-Carlo: a few dozen points */
            int n = c->climate_nmax > 0 ? c->climate_nmax : 12;
            v = 8 + 2.0 * n;
            break;
        }
        case CTYPE_FIRST_STRONGHOLD: v = 5; break;
        case CTYPE_HEIGHT:          v = 40; break;   /* needs applySeed */
        case CTYPE_SPIRAL:          v = 0.5; break;  /* + positions × child (subtree) */
        case CTYPE_BIOME_FILTER:
            v = 20 + 0.05 * area_cells(c, c->biome_scale > 0 ? c->biome_scale : 4);
            break;
        case CTYPE_STRUCTURE_OW:    v = 60; break;
        case CTYPE_STRUCTURE_NETHER:v = 60; break;
        case CTYPE_STRUCTURE_END:   v = 60; break;
        case CTYPE_BIOME_CENTER:
            v = 80 + 0.05 * area_cells(c, c->biome_scale > 0 ? c->biome_scale : 4);
            break;
        case CTYPE_BIOME_SAMPLE:    v = 300; break;   /* monteCarloBiomes */
        case CTYPE_SPAWN:           v = 3000; break;  /* getSpawn is very expensive */
        case CTYPE_STRONGHOLD:      v = 4000; break;  /* nextStronghold iterates rings */
        default:                    v = 1000; break;
    }
    /* integer units of 0.1 µs, bounded */
    double u = v * 10.0;
    if (u < 1) u = 1;
    if (u > 1e9) u = 1e9;
    return (int)u;
}

static int ssa_test_seed(SsaEnv *env, const SsaCond *conds, int n_conds,
                          CondCache *caches)
{
    env_reset_positions(env);
    for (int i = 0; i < n_conds; i++) {
        if (conds[i].parent_idx == 0 && conds[i].cond_idx > 0) {
            /* Reset positions before each top-level eval to ensure no
             * cross-contamination from previous subtree positions. */
            env_reset_positions(env);
            if (!eval_node(env, conds, n_conds, caches,
                             conds[i].cond_idx, 0, 0))
                return 0;
        }
    }
    return 1;
}

/* Sorted version using pre-computed eval order. Same logic but iterates
 * cheap-first to fail fast on expensive conds. */
/* Une condition top-level est « coûteuse » si son évaluation complète est
 * très chère par graine et qu'on a donc intérêt à la DIFFÉRER après les
 * conditions bon marché (façon cubiomes-viewer : passe rapide PASS_FAST_48
 * qui saute ces filtres, puis passe complète sur les rares survivantes).
 * Le cas critique : biome_filter à l'échelle 1 sur grande zone (checkForBiomes
 * scanne des millions de colonnes). On y inclut aussi les filtres biome lourds
 * et le spawn/stronghold. */
static int cond_is_expensive(const SsaCond *c) {
    switch (c->ctype) {
        case CTYPE_BIOME_FILTER:
            /* scale 1 (biome_scale==1) = extrêmement cher ; scale 4+ raisonnable.
             * On diffère systématiquement le biome_filter (il dépend des 64 bits
             * et coûte cher), surtout à scale fin. */
            return 1;
        case CTYPE_BIOME_SAMPLE:
        case CTYPE_BIOME_CENTER:
        case CTYPE_SPAWN:
        case CTYPE_STRONGHOLD:
            return 1;
        default:
            return 0;
    }
}

/* Vrai si la condition coûteuse 'c' peut être différée sans risque, i.e.
 * elle n'a pas d'enfants et aucune autre condition ne pointe vers elle via
 * 'relative' (sinon différer changerait la position vue par une dépendance). */
static int cond_deferrable(const SsaCond *conds, int n_conds, int idx) {
    if (!cond_is_expensive(&conds[idx])) return 0;
    int my1 = conds[idx].cond_idx;       /* identifiant 1-based */
    for (int j = 0; j < n_conds; j++) {
        if (j == idx) continue;
        if (conds[j].parent_idx == my1) return 0;  /* a un enfant */
        /* parent_idx encode la relation 'relative'/'parent' : si une autre
         * condition dépend de celle-ci, on ne diffère pas. */
    }
    return 1;
}

static int ssa_test_seed_sorted(SsaEnv *env, const SsaCond *conds, int n_conds,
                                  CondCache *caches,
                                  const int *order, int n_order)
{
    /* PASSE 1 (rapide) : toutes les conditions top-level SAUF les coûteuses
     * différables. Rejette la grande majorité des graines à faible coût.
     * (Façon cubiomes-viewer : PASS_FAST_48 saute les filtres biome lourds.) */
    for (int oi = 0; oi < n_order; oi++) {
        int i = order[oi];
        if (cond_deferrable(conds, n_conds, i)) continue;  /* → passe 2 */
        env_reset_positions_n(env, n_conds);
        if (!eval_node(env, conds, n_conds, caches,
                         conds[i].cond_idx, 0, 0))
            return 0;
    }
    /* PASSE 2 (lente) : uniquement les conditions coûteuses différables, et
     * seulement maintenant que tout le reste est satisfait. */
    for (int oi = 0; oi < n_order; oi++) {
        int i = order[oi];
        if (!cond_deferrable(conds, n_conds, i)) continue;
        env_reset_positions_n(env, n_conds);
        if (!eval_node(env, conds, n_conds, caches,
                         conds[i].cond_idx, 0, 0))
            return 0;
    }
    return 1;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * Engine state + threading
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef void (*MatchCallback)(int64_t seed, void *user_data);

typedef struct {
    const SsaCond   *conds;
    int              n_conds;
    int              mc;
    int              large;

    int64_t          seed_start;
    int64_t          seed_end;
    /* Bounded ranges are tracked as a count of issued items (unsigned), not by
     * comparing with seed_end: a range of NEGATIVE seeds has seed_end < 0,
     * which the old "hard_end > 0" test took for "no end" → the search never
     * stopped. has_end = 0 means unlimited. */
    int              has_end;
    uint64_t         span;
    uint64_t         issued;

    int64_t          item_size;
    int              search_mode;
    /* 0 = SSA_MODE_INC    : seed++, full check with lazy applySeed,
     *                       emit individual seeds in order.
     * 1 = SSA_MODE_FAM48  : 0..MASK48, emit one rep seed per viable 48-bit family.
     * 2 = SSA_MODE_BLOCKS : 48-bit prefilter + expand to all 65536 highs.
     *
     * INC and FAM48 require the conditions to be position-only eligible
     * (i.e. only structure conditions). Otherwise we fall back to INC with
     * no lazy optim (correctness over speed).
     */
    int              pos_only_eligible;

    /* Pre-sorted top-level eval order (cheapest first). Index into `conds`. */
    int              toplevel_order[MAX_CONDS];
    int              n_toplevel;

    MUTEX_T          work_mutex;
    int64_t          next_seed;
    int64_t          seeds_done;
    int              is_done;
    volatile int    *stop_flag;
    volatile int     active_workers;

    MUTEX_T          result_mutex;
    MatchCallback    callback;
    void            *user_data;

    volatile int64_t progress;
    volatile int64_t found_count;
    /* p96: stop by itself once this many matches were found (0 = no limit).
     * The result limit / safety cap used to be applied by Python after the
     * drain: on a session where every seed matches, the engine was a whole
     * ring (1 M matches) ahead → 1.5 M results shown for a 100 k limit. */
    volatile int64_t max_found;

    /* Lock-free match ring buffer — avoids Python GIL per-match. */
#define RING_CAP (1 << 20)
    volatile int64_t *ring_buf;   /* volatile: slot written before head moves */
    volatile int64_t ring_head;
    volatile int64_t ring_tail;
    ChildOrderCache *co;          /* evaluation order owned by this search */

    /* Resume point (pause / resume): start of the chunk each worker is on,
     * INT64_MAX when idle. Every seed before min(next_seed, cur_chunk[]) has
     * been fully tested → restarting from there never skips a seed. */
    volatile int64_t cur_chunk[MAX_THREADS];
    volatile int     cur_busy[MAX_THREADS];
    int              n_slots;
    int64_t          iter_origin;  /* first seed/low of the scan (resume math) */
    /* merged profiler (under work_mutex) */
    int64_t          prof_reached[MAX_CONDS];
    int64_t          prof_checks[MAX_CONDS];
    int64_t          prof_checks_ok[MAX_CONDS];
    int64_t          prof_passed[MAX_CONDS];
    uint64_t         prof_ticks[MAX_CONDS];
    uint64_t         prof_total;
    int              joined;
} EngineState;

static void prof_merge(EngineState *st, SsaProf *pf, int n)
{   /* caller holds work_mutex */
    if (!pf) return;
    if (n > MAX_CONDS) n = MAX_CONDS;
    for (int i = 0; i < n; i++) {
        st->prof_reached[i] += pf->reached[i]; pf->reached[i] = 0;
        st->prof_passed[i]  += pf->passed[i];  pf->passed[i] = 0;
        st->prof_checks[i]  += pf->checks[i];  pf->checks[i] = 0;
        st->prof_checks_ok[i] += pf->checks_ok[i]; pf->checks_ok[i] = 0;
        st->prof_ticks[i]   += pf->ticks[i];   pf->ticks[i] = 0;
    }
    st->prof_total += pf->total_ticks; pf->total_ticks = 0;
}

/* Push a match into the ring. Called with result_mutex held. If the reader
 * (Python drain) is behind by a whole ring, wait instead of overwriting
 * unread results (before: silent loss past 1M pending matches). */
static void ring_push(EngineState *st, int64_t v) {
    while (st->ring_head - st->ring_tail >= RING_CAP && !*st->stop_flag) {
        MUTEX_UNLOCK(st->result_mutex);
#ifdef _WIN32
        Sleep(1);
#else
        { struct timespec ts = {0, 1000000}; nanosleep(&ts, NULL); }
#endif
        MUTEX_LOCK(st->result_mutex);
    }
    int64_t h = st->ring_head;
    st->ring_buf[h & (RING_CAP-1)] = v;
    st->ring_head = h + 1;
}

static int64_t now_ns(void) {
#ifdef _WIN32
    LARGE_INTEGER f, t;
    QueryPerformanceFrequency(&f);
    QueryPerformanceCounter(&t);
    return (int64_t)(t.QuadPart * 1000000000LL / f.QuadPart);
#else
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000000LL + ts.tv_nsec;
#endif
}

/* Stop arrived DURING this seed's test: the evaluation was cut short (a spiral,
 * a stronghold scan, a biome check return « fail » when interrupted), so its
 * answer is meaningless — under a NOT it even turned into a PASS: every seed in
 * flight at Stop was published as a result (CoffeeAddicted 30/09, consecutive
 * false seeds, one run per thread). Its chunk is left unfinished, so a resume
 * tests it again. */
#define STOP_DURING_TEST() do { if (*st->stop_flag) goto done; } while (0)

static void worker_run(EngineState *st) {
    SsaEnv env;
    memset(&env, 0, sizeof(env));
    env.co = st->co;
    env.mc = st->mc;
    env.large = st->large;
    env.current_dim = -2;
    env.gbn_seed = ~0ULL;
    env.gbn_nmax = -999;
    env.stop_flag = st->stop_flag;  /* arrêt réactif dans les boucles spiral */
    SsaProf *prof = (SsaProf *)calloc(1, sizeof(SsaProf));
    env.prof = prof;
    /* Invalidate climate cache slots (tous les nmax) */
    for (int i = 0; i < NP_MAX; i++) {
        for (int s = 0; s < CLIM_NSLOT; s++) {
            env.climate_cache_nmax[i][s] = -2;
            env.climate_cache_seed[i][s] = 0xdeadbeefcafebabeULL; env.clazy[i].valid = 0;
        }
    }
    uint32_t flags = st->large ? LARGE_BIOMES : 0;
    setupGenerator(&env.g, st->mc, flags);

    CondCache caches[MAX_CONDS];
    memset(caches, 0, sizeof(caches));

    /* Each thread tracks its own adaptive chunk size to avoid false sharing
     * and contention when multiple slow-condition threads all shrink item_size
     * simultaneously. Start at a moderate size for good throughput; the
     * "Test seed" button covers the case where the user wants to validate a
     * specific starting seed instantly. */
    /* Small first chunks, then ×2 per fast chunk (target ~100 ms): the resume
     * point only moves when an item is finished, and 1024 slow seeds (a biome
     * filter at ~0.2 s/seed) meant minutes before the first Pause could keep
     * anything, and seconds before Stop was seen (audit 28/09). BLOCKS: one
     * low can expand to 65 536 full checks → 1. */
    int64_t local_item_size = (st->search_mode == SSA_MODE_BLOCKS) ? 1 : 16;
    MUTEX_LOCK(st->work_mutex);
    int slot = st->n_slots++;
    if (slot < MAX_THREADS) { st->cur_chunk[slot] = INT64_MAX; st->cur_busy[slot] = 0; }
    MUTEX_UNLOCK(st->work_mutex);
    while (1) {
        if (*st->stop_flag) break;

        MUTEX_LOCK(st->work_mutex);
        if (st->is_done) { MUTEX_UNLOCK(st->work_mutex); break; }
        int64_t chunk_start = st->next_seed;
        int64_t chunk_size  = local_item_size;
        int64_t local_done = 0;  /* graines déjà publiées intra-chunk (INC) */
        /* In FAM48 / BLOCKS modes, the iteration space is the 48-bit lows
         * (0..MASK48). In INC mode, it's seed_start..seed_end (or 2^64 if 0). */
        if (st->has_end) {
            uint64_t remaining = st->span - st->issued;
            if (remaining == 0) {
                st->is_done = 1;
                MUTEX_UNLOCK(st->work_mutex);
                break;
            }
            if ((uint64_t)chunk_size > remaining) chunk_size = (int64_t)remaining;
        }
        st->next_seed = (int64_t)((uint64_t)st->next_seed + (uint64_t)chunk_size);
        st->issued += (uint64_t)chunk_size;
        if (st->has_end && st->issued >= st->span) st->is_done = 1;
        if (slot < MAX_THREADS) { st->cur_chunk[slot] = chunk_start; st->cur_busy[slot] = 1; }
        MUTEX_UNLOCK(st->work_mutex);

        int64_t t0 = now_ns();

        if (st->search_mode == SSA_MODE_BLOCKS) {
            /* BLOCKS: chunk_start..chunk_start+chunk_size are 48-bit lows.
             * Pos-only check on the low; if pass, expand 65536 highs with full check.
             * Progress is published inside the chunk (every expanded low and
             * every 4096 highs): one expansion can take seconds, and a chunk
             * of 64 lows with several expansions left the counter at 0 for a
             * long time (flat progress, unmeasurable speed). local_done counts
             * the SEEDS already published; the chunk end adds the rest. */
            for (int64_t i = 0; i < chunk_size; i++) {
                if (*st->stop_flag) goto done;
                uint64_t low = (uint64_t)(chunk_start + i) & 0xFFFFFFFFFFFFULL;
                env.seed = low;
                env.sn_ow_ready = 0;
                env.sn_end_ready = 0;
                env.current_dim = -2;
                /* Pos-only check: no applySeed needed yet for getStructurePos */
                int pok = ssa_test_seed_pos_only(&env, st->conds, st->n_conds, caches);
                STOP_DURING_TEST();
                if (!pok)
                    continue;  /* skip all 65536 highs */

                /* Now expand: test each high with full check */
                {   /* publish the lows skipped before this one */
                    int64_t upto = i * 0x10000LL;
                    MUTEX_LOCK(st->work_mutex);
                    st->seeds_done += upto - local_done;
                    st->progress = st->seeds_done;
                    if (slot < MAX_THREADS) st->cur_chunk[slot] = chunk_start + i;   /* resume here */
                    MUTEX_UNLOCK(st->work_mutex);
                    local_done = upto;
                }
                for (int64_t h = 0; h < 0x10000LL; h++) {
                    if (*st->stop_flag) goto done;
                    if ((h & 4095) == 4095) {
                        int64_t upto = i * 0x10000LL + h + 1;
                        MUTEX_LOCK(st->work_mutex);
                        st->seeds_done += upto - local_done;
                        st->progress = st->seeds_done;
                        MUTEX_UNLOCK(st->work_mutex);
                        local_done = upto;
                    }
                    uint64_t full = (h << 48) | low;
                    env.seed = full;
                    env.sn_ow_ready = 0;
                    env.sn_end_ready = 0;
                    env.current_dim = -2;
                    prof_seed_begin(prof);
                    uint64_t pt0 = (prof && prof->timing) ? SSA_TICKS() : 0;
                    applySeed(&env.g, DIM_OVERWORLD, full);
                    env.current_dim = DIM_OVERWORLD;
                    int bok = ssa_test_seed_sorted(&env, st->conds, st->n_conds, caches, st->toplevel_order, st->n_toplevel);
                    if (prof && prof->timing) prof->total_ticks += SSA_TICKS() - pt0;
                    STOP_DURING_TEST();
                    if (bok) {
                        MUTEX_LOCK(st->result_mutex);
                        if (st->callback) st->callback((int64_t)full, st->user_data);
                        ring_push(st, (int64_t)full);
                        st->found_count++;
                        if (st->max_found > 0 && st->found_count >= st->max_found) *st->stop_flag = 1;
                        MUTEX_UNLOCK(st->result_mutex);
                    }
                }
            }
        } else if (st->search_mode == SSA_MODE_FAM48) {
            /* FAM48: emit one representative seed per viable 48-bit family. */
            for (int64_t i = 0; i < chunk_size; i++) {
                if (*st->stop_flag) goto done;
                if (local_item_size <= 64 || (i & 63) == 0) {   /* exact resume point */
                    MUTEX_LOCK(st->work_mutex);
                    if (slot < MAX_THREADS) st->cur_chunk[slot] = chunk_start + i;
                    MUTEX_UNLOCK(st->work_mutex);
                }
                uint64_t low = (uint64_t)(chunk_start + i) & 0xFFFFFFFFFFFFULL;
                env.seed = low;
                env.sn_ow_ready = 0;
                env.sn_end_ready = 0;
                env.current_dim = -2;
                /* No applySeed needed: getStructurePos is pure RNG arithmetic */
                int fok = ssa_test_seed_pos_only(&env, st->conds, st->n_conds, caches);
                STOP_DURING_TEST();
                if (fok) {
                    MUTEX_LOCK(st->result_mutex);
                    if (st->callback) st->callback((int64_t)low, st->user_data);
                    ring_push(st, (int64_t)low);
                    st->found_count++;
                    if (st->max_found > 0 && st->found_count >= st->max_found) *st->stop_flag = 1;
                    MUTEX_UNLOCK(st->result_mutex);
                }
            }
        } else {
            /* INC: seed++, lazy applySeed pattern.
             * If conditions are pos-only eligible (structures only), first do
             * a pos-only check (no applySeed needed). Only if pass, then
             * applySeed + full check. */
            int pos_only = st->pos_only_eligible;
            int stop_check_counter = 0;
            for (int64_t i = 0; i < chunk_size; i++) {
                /* Stop + publication best-effort. La fréquence s'adapte : quand
                 * le chunk est petit (item_size faible = graines lentes, ex.
                 * biome_filter scale 1), on publie/teste souvent pour que le
                 * débit affiché ne reste pas figé à 0 et que l'arrêt réponde
                 * vite. Quand le chunk est gros (graines rapides), on teste
                 * rarement pour éviter la contention cache qui réduit le débit. */
                int check_mask = (local_item_size <= 64) ? 0 : 63;
                if ((++stop_check_counter & check_mask) == 0) {
                    if (*st->stop_flag) goto done;
                    MUTEX_LOCK(st->work_mutex);
                    st->seeds_done += (i - local_done);
                    st->progress = st->seeds_done;
                    local_done = i;
                    /* seeds before chunk_start+i are fully tested: the resume
                     * point may move up to here (exact to the seed) */
                    if (slot < MAX_THREADS) st->cur_chunk[slot] = chunk_start + i;
                    MUTEX_UNLOCK(st->work_mutex);
                }

                int64_t seed = chunk_start + i;
                uint64_t useed = (uint64_t)seed;
                env.seed = useed;
                env.sn_ow_ready = 0;
                env.sn_end_ready = 0;
                prof_seed_begin(prof);
                uint64_t pt0 = (prof && prof->timing) ? SSA_TICKS() : 0;

                if (pos_only) {
                    /* Fast pre-check: no applySeed yet */
                    env.current_dim = -2;
                    int qok = ssa_test_seed_pos_only(&env, st->conds, st->n_conds, caches);
                    STOP_DURING_TEST();
                    if (!qok) {
                        if (prof && prof->timing) prof->total_ticks += SSA_TICKS() - pt0;
                        continue;
                    }
                }

                /* Full check. We DON'T call applySeed eagerly here anymore
                 * — many conditions (climate_minmax, slime, logic_*, spiral,
                 * scale_to_*) don't need an initialized Generator. They use
                 * env->climate_cache or pure arithmetic instead.
                 *
                 * For conditions that DO need it (biome_sample, biome_filter,
                 * height, structures, spawn, stronghold), the eval code calls
                 * env_set_dim(DIM_OVERWORLD) which triggers applySeed on the
                 * first such access per seed (current_dim was reset to -2).
                 * This saves the ~30µs applySeed cost on seeds that fail
                 * early on a cheap climate or logic condition. */
                env.current_dim = -2;
                int iok = ssa_test_seed_sorted(&env, st->conds, st->n_conds, caches, st->toplevel_order, st->n_toplevel);
                if (prof && prof->timing) prof->total_ticks += SSA_TICKS() - pt0;
                STOP_DURING_TEST();
                if (iok) {
                    MUTEX_LOCK(st->result_mutex);
                    if (st->callback) st->callback(seed, st->user_data);
                    ring_push(st, seed);
                    st->found_count++;
                    if (st->max_found > 0 && st->found_count >= st->max_found) *st->stop_flag = 1;
                    MUTEX_UNLOCK(st->result_mutex);
                }
            }
            /* En mode INC, seeds_done est maintenant bumpé sous le work_mutex
             * en fin de chunk (cf. plus bas). Aucune opération atomique dans
             * la boucle de graines. */
        }

        int64_t t1 = now_ns();
        int64_t dt_ms = (t1 - t0) / 1000000;

        MUTEX_LOCK(st->work_mutex);
        if (st->search_mode == SSA_MODE_BLOCKS) {
            /* Each 'low' processed represents 65536 actual seeds (minus what
             * was already published inside the chunk) */
            st->seeds_done += chunk_size * 0x10000LL - local_done;
        } else if (st->search_mode == SSA_MODE_INC) {
            /* La publication intra-chunk n'était qu'un store best-effort de
             * progress ; le compteur réel seeds_done est avancé ici en fin de
             * chunk (local_done reste 0). */
            st->seeds_done += (chunk_size - local_done);
        } else {
            st->seeds_done += chunk_size;
        }
        st->progress    = st->seeds_done;
        /* Adaptatif : viser ~100ms/chunk. On démarre à 1 (la graine de départ
         * est émise immédiatement, comme cubiomes-viewer) puis ×2 par chunk
         * rapide jusqu'au plafond 2^16. */
        if (dt_ms < 50  && local_item_size < (1<<16)) local_item_size *= 2;
        else if (dt_ms > 200 && local_item_size > 1)  local_item_size /= 2;
        /* Expose the current local size to EngineState (best-effort, under mutex) */
        st->item_size = local_item_size;
        if (slot < MAX_THREADS) { st->cur_chunk[slot] = INT64_MAX; st->cur_busy[slot] = 0; }   /* chunk complete */
        prof_merge(st, prof, st->n_conds);
        MUTEX_UNLOCK(st->work_mutex);
    }
done:;
    MUTEX_LOCK(st->work_mutex);
    prof_merge(st, prof, st->n_conds);
    st->active_workers--;
    MUTEX_UNLOCK(st->work_mutex);
    free(prof);
}

#ifdef _WIN32
static unsigned __stdcall worker_thread(void *arg) {
    worker_run((EngineState*)arg);
    return 0;
}
#else
static void *worker_thread(void *arg) {
    worker_run((EngineState*)arg);
    return NULL;
}
#endif

/* ═══════════════════════════════════════════════════════════════════════════
 * Public API
 * ═══════════════════════════════════════════════════════════════════════════ */
typedef struct {
    EngineState  state;
    THREAD_T     threads[MAX_THREADS];
    int          n_threads;
    int          stop_flag;
} SsaEngine;

EXPORT SsaEngine *ssa_engine_create(
    const SsaCond *conds, int n_conds,
    int mc, int large,
    int64_t seed_start, int64_t seed_count,
    int n_threads,
    MatchCallback callback, void *user_data)
{
    if (n_threads < 1) n_threads = 1;
    if (n_threads > MAX_THREADS) n_threads = MAX_THREADS;
    if (n_conds <= 0 || n_conds > MAX_CONDS) return NULL;

    SsaEngine *eng = (SsaEngine*)calloc(1, sizeof(SsaEngine));
    if (!eng) return NULL;

    EngineState *st = &eng->state;
    st->conds = conds; st->n_conds = n_conds;
    /* Pré-calcule l'ordre d'évaluation trié (cheap-first) une seule fois pour
     * toute la recherche, au lieu de le recalculer par graine dans eval_node. */
    st->co = child_order_new(conds, n_conds);
    if (!st->co) { free(eng); return NULL; }
    st->mc = mc; st->large = large;
    st->seed_start = seed_start;
    /* next_seed / seed_end sont fixés plus bas, APRÈS la détermination du
     * search_mode effectif (l'origine diffère en FAM48/BLOCKS vs INC). */
    st->next_seed = seed_start;
    st->seed_end  = (seed_count > 0) ? (seed_start + seed_count) : 0;
    st->seeds_done = 0;
    st->is_done = 0;
    st->stop_flag = &eng->stop_flag;
    st->callback = callback;
    st->user_data = user_data;
    st->progress = 0;
    st->found_count = 0;
    st->ring_buf  = (volatile int64_t*)calloc(RING_CAP, sizeof(int64_t));
    st->ring_head = 0;
    st->ring_tail = 0;
    st->item_size = 16;
    /* Decode `large` into actual large flag + mode code:
     *   bit 0    = large biomes
     *   bits 8-15 = search mode code
     */
    int large_biomes = large & 1;
    int requested_mode = (large >> 8) & 0xFF;
    /* Update env's large flag */
    st->large = large_biomes;
    st->pos_only_eligible = conditions_eligible_for_2pass(conds, n_conds);

    /* Build a sorted eval order for top-level conditions: cheapest first.
     * This lets us reject seeds on a fast condition before paying the cost
     * of an expensive one (Monte Carlo, getSpawn, etc.). */
    {
        int idxs[MAX_CONDS]; int64_t costs[MAX_CONDS]; int n_top = 0;
        for (int i = 0; i < n_conds && n_top < MAX_CONDS; i++) {
            if (conds[i].parent_idx == 0 && conds[i].cond_idx > 0) {
                idxs[n_top]  = i;
                costs[n_top] = cond_subtree_cost(conds, n_conds, conds[i].cond_idx);
                n_top++;
            }
        }
        /* Stable insertion sort by cost — preserves original order on ties
         * so root climate subtrees still run first when they are the cheapest
         * (which they are). */
        for (int a = 1; a < n_top; a++) {
            int idx_a = idxs[a]; int64_t cost_a = costs[a];
            int b = a - 1;
            while (b >= 0 && costs[b] > cost_a) {
                idxs[b+1] = idxs[b]; costs[b+1] = costs[b];
                b--;
            }
            idxs[b+1] = idx_a; costs[b+1] = cost_a;
        }
        for (int i = 0; i < n_top; i++) st->toplevel_order[i] = idxs[i];
        st->n_toplevel = n_top;
    }
    /* ── Auto-adaptation du mode (option 2, façon cubiomes) ────────────────
     * Le mode demandé par l'utilisateur est interprété intelligemment :
     *
     *  - "48-bit family" (FAM48) devient ADAPTATIF :
     *      • structures dep48 seules        → FAM48 pur (1 représentant/famille)
     *      • structures dep48 + dep64        → BLOCKS (préfiltre 48-bit sur les
     *        (biomes/climats/height/…)         structures, puis expand les 65536
     *                                          bits hauts en testant TOUT — exact)
     *      • aucune structure dep48          → INC (balayage incrémental normal,
     *                                          comme cubiomes quand slist est vide)
     *
     *  - "Blocks" force la stratégie BLOCKS (préfiltre + expand) dès qu'il y a
     *    au moins une structure ; sinon INC.
     *
     *  - "Incremental" reste INC.
     *
     * Dans tous les cas le RÉSULTAT est exact : BLOCKS et INC testent l'arbre
     * complet sur chaque seed 64 bits ; FAM48 pur n'est utilisé que lorsque la
     * session ne contient que des conditions dep48 (donc indépendantes des bits
     * hauts), où émettre un représentant par famille est correct et suffisant. */
    int n_dep48 = 0, n_dep64 = 0;
    classify_session(conds, n_conds, &n_dep48, &n_dep64);

    if (requested_mode == SSA_MODE_FAM48) {
        if (n_dep48 > 0 && n_dep64 == 0) {
            /* Structures seules. La POSITION ne dépend que des 48 bits, mais la
             * VIABILITÉ (biome) dépend des 64 bits : émettre un simple low
             * (FAM48 pur) donnerait des faux positifs (position candidate sans
             * biome valide). On utilise donc BLOCKS : pré-filtre 48-bit sur la
             * position, puis expansion des 65536 bits hauts avec validation
             * complète du biome. Résultats exacts, comme cubiomes. */
            st->search_mode = SSA_MODE_BLOCKS;
        } else if (n_dep48 > 0 && n_dep64 > 0) {
            st->search_mode = SSA_MODE_BLOCKS;  /* mixte → préfiltre + expand */
        } else {
            st->search_mode = SSA_MODE_INC;     /* pas de structure → incrémental */
        }
    } else if (requested_mode == SSA_MODE_BLOCKS) {
        st->search_mode = (n_dep48 > 0) ? SSA_MODE_BLOCKS : SSA_MODE_INC;
    } else {
        st->search_mode = SSA_MODE_INC;
    }

    /* ── Origine du balayage selon le mode EFFECTIF ────────────────────────
     * En FAM48/BLOCKS, l'espace d'itération est celui des LOWS 48 bits
     * (0..2^48). Un seed_start arbitraire (seed aléatoire de l'UI, souvent
     * proche de 2^62) ne doit donc PAS servir d'origine : sinon chunk_start
     * dépasse 2^48 et la recherche se termine instantanément (remaining<0).
     * On démarre à seed_start masqué sur 48 bits (respecte une reprise), la
     * borne de fin restant 2^48 (gérée via hard_end dans la boucle). */
    st->issued = 0;
    if (st->search_mode == SSA_MODE_FAM48 || st->search_mode == SSA_MODE_BLOCKS) {
        /* iteration space = the 2^48 lows */
        st->next_seed = seed_start & 0xFFFFFFFFFFFFLL;
        uint64_t left = 0x1000000000000ULL - (uint64_t)st->next_seed;
        st->has_end = 1;
        st->span = (seed_count > 0 && (uint64_t)seed_count < left) ? (uint64_t)seed_count : left;
        st->seed_end = st->next_seed + (int64_t)st->span;
    } else {
        st->has_end = seed_count > 0;
        st->span = seed_count > 0 ? (uint64_t)seed_count : 0;
        /* "48-bit family" / "Blocks" requested but the session has no
         * structure → Incremental. The caller's count is in 48-bit units
         * (families of 65 536 seeds): scale it, otherwise a 1 M-seed limit
         * searched 16 seeds and reported 1 M searched. */
        if (requested_mode != SSA_MODE_INC && seed_count > 0)
            st->span = ((uint64_t)seed_count > (UINT64_MAX >> 16)) ? UINT64_MAX
                                                                   : (uint64_t)seed_count << 16;
    }
    st->iter_origin = st->next_seed;
    if (st->search_mode == SSA_MODE_BLOCKS) {
        st->item_size = 64;  /* expand factor 65536 */
    } else if (st->search_mode == SSA_MODE_FAM48) {
        st->item_size = 4096;  /* lots of fast checks per low */
    }
    st->active_workers = n_threads;

    MUTEX_INIT(st->work_mutex);
    MUTEX_INIT(st->result_mutex);

    eng->n_threads = n_threads;
    for (int i = 0; i < n_threads; i++) {
#ifdef _WIN32
        eng->threads[i] = (HANDLE)_beginthreadex(NULL, 0, worker_thread, st, 0, NULL);
#else
        pthread_create(&eng->threads[i], NULL, worker_thread, st);
#endif
    }
    return eng;
}

/* p96: the engine stops by itself after n matches (0 = no limit). Set it
 * right after ssa_engine_create. */
EXPORT void ssa_engine_set_max_found(SsaEngine *eng, int64_t n) {
    if (eng) eng->state.max_found = n > 0 ? n : 0;
    if (eng && n > 0 && eng->state.found_count >= n) eng->stop_flag = 1;
}

EXPORT void ssa_engine_stop(SsaEngine *eng) {
    if (eng) eng->stop_flag = 1;
}

/* Wait for the workers WITHOUT freeing the engine: afterwards the results,
 * the resume point and the profile are final (read them, then _wait). */
EXPORT void ssa_engine_join(SsaEngine *eng) {
    if (!eng || eng->state.joined) return;
    for (int i = 0; i < eng->n_threads; i++) {
#ifdef _WIN32
        WaitForSingleObject(eng->threads[i], INFINITE);
        CloseHandle(eng->threads[i]);
#else
        pthread_join(eng->threads[i], NULL);
#endif
    }
    eng->state.joined = 1;
}

/* Profile: out[3*i..3*i+2] = seeds that reached condition i, seeds that
 * passed it, its own CPU ticks (sampled); out[3*n] = ticks of whole seed
 * tests (same sampling). Returns n (conditions) or 0. */
EXPORT int ssa_engine_profile(SsaEngine *eng, int64_t *out, int max_conds) {
    if (!eng || !out) return 0;
    EngineState *st = &eng->state;
    int n = st->n_conds < max_conds ? st->n_conds : max_conds;
    if (n > MAX_CONDS) n = MAX_CONDS;
    MUTEX_LOCK(st->work_mutex);
    for (int i = 0; i < n; i++) {
        out[3 * i] = st->prof_reached[i];
        out[3 * i + 1] = st->prof_passed[i];
        out[3 * i + 2] = (int64_t)st->prof_ticks[i];
    }
    out[3 * n] = (int64_t)st->prof_total;
    MUTEX_UNLOCK(st->work_mutex);
    return n;
}

/* Same as ssa_engine_profile with 5 values per condition: seeds reached,
 * seeds whose branch passed, own CPU ticks, own checks, own checks passed;
 * then the total ticks. */
EXPORT int ssa_engine_profile2(SsaEngine *eng, int64_t *out, int max_conds) {
    if (!eng || !out) return 0;
    EngineState *st = &eng->state;
    int n = st->n_conds < max_conds ? st->n_conds : max_conds;
    if (n > MAX_CONDS) n = MAX_CONDS;
    MUTEX_LOCK(st->work_mutex);
    for (int i = 0; i < n; i++) {
        out[5 * i] = st->prof_reached[i];
        out[5 * i + 1] = st->prof_passed[i];
        out[5 * i + 2] = (int64_t)st->prof_ticks[i];
        out[5 * i + 3] = st->prof_checks[i];
        out[5 * i + 4] = st->prof_checks_ok[i];
    }
    out[5 * n] = (int64_t)st->prof_total;
    MUTEX_UNLOCK(st->work_mutex);
    return n;
}

EXPORT void ssa_engine_wait(SsaEngine *eng) {
    if (!eng) return;
    ssa_engine_join(eng);
    MUTEX_DESTROY(eng->state.work_mutex);
    MUTEX_DESTROY(eng->state.result_mutex);
    if (eng->state.ring_buf) free((void *)eng->state.ring_buf);
    free(eng->state.co);
    free(eng);
}

EXPORT int ssa_engine_effective_mode(SsaEngine *eng) {
    /* Renvoie le mode RÉELLEMENT utilisé après auto-adaptation :
     * 0=INC, 1=FAM48, 2=BLOCKS. Permet à l'UI d'afficher ce que le moteur a
     * choisi (ex. "48-bit family" → BLOCKS car la session est mixte). */
    return eng ? eng->state.search_mode : -1;
}

EXPORT int ssa_engine_progress(SsaEngine *eng,
                                 int64_t *out_done, int64_t *out_found) {
    if (!eng) { if(out_done)*out_done=0; if(out_found)*out_found=0; return 0; }
    if (out_done)  *out_done  = eng->state.progress;
    if (out_found) *out_found = eng->state.found_count;
    return (eng->state.active_workers > 0) ? 1 : 0;
}

/* Drain up to max_out matches from the ring buffer.
 * Returns number written. Python calls this instead of using a callback
 * to avoid acquiring the GIL on every single match. */
/* First seed (iteration-space units: seed in INC, 48-bit low in FAM48 /
 * BLOCKS) not yet fully tested. Restarting a search there, in the same mode,
 * re-tests at most one chunk per thread and never skips a seed. */
EXPORT int64_t ssa_engine_resume_point(SsaEngine *eng) {
    if (!eng) return 0;
    EngineState *st = &eng->state;
    MUTEX_LOCK(st->work_mutex);
    /* compared as unsigned offsets from the scan origin: an Incremental scan
     * that crosses INT64_MAX continues at INT64_MIN, and a plain signed min()
     * then picked the wrapped next_seed and skipped the chunks in progress */
    uint64_t o = (uint64_t)st->iter_origin;
    uint64_t best = (uint64_t)st->next_seed - o;
    int n = st->n_slots < MAX_THREADS ? st->n_slots : MAX_THREADS;
    for (int i = 0; i < n; i++)
        if (st->cur_busy[i] && (uint64_t)st->cur_chunk[i] - o < best)
            best = (uint64_t)st->cur_chunk[i] - o;
    MUTEX_UNLOCK(st->work_mutex);
    return (int64_t)(o + best);
}

EXPORT int ssa_engine_drain_matches(SsaEngine *eng,
                                     int64_t *out_seeds, int max_out) {
    if (!eng || !out_seeds || max_out <= 0) return 0;
    EngineState *st = &eng->state;
    int n = 0;
    while (n < max_out) {
        int64_t tail = st->ring_tail;
        if (tail >= st->ring_head) break;
        out_seeds[n++] = st->ring_buf[tail & (RING_CAP-1)];
        st->ring_tail = tail + 1;
    }
    return n;
}

EXPORT size_t ssa_sizeof_ssacond(void) { return sizeof(SsaCond); }

EXPORT int ssa_ctype_spiral(void)             { return CTYPE_SPIRAL; }
EXPORT int ssa_ctype_scale_to_nether(void)    { return CTYPE_SCALE_TO_NETHER; }
EXPORT int ssa_ctype_scale_to_overworld(void) { return CTYPE_SCALE_TO_OVERWORLD; }
EXPORT int ssa_ctype_first_stronghold(void)   { return CTYPE_FIRST_STRONGHOLD; }
EXPORT int ssa_ctype_stronghold(void)         { return CTYPE_STRONGHOLD; }
EXPORT int ssa_ctype_structure_ow(void)       { return CTYPE_STRUCTURE_OW; }

/* Récupère salt/regionSize/chunkRange d'une structure pour préparer les jobs
 * GPU (sans réimplémenter getStructureConfig sur GPU). 1=ok, 0=structure sans config. */
EXPORT int ssa_get_struct_config(int struct_type, int mc,
        int32_t *out_salt, int32_t *out_region_size, int32_t *out_chunk_range) {
    StructureConfig sc;
    if (!getStructureConfig(struct_type, mc, &sc)) return 0;
    if (out_salt)        *out_salt        = sc.salt;
    if (out_region_size) *out_region_size = sc.regionSize;
    if (out_chunk_range) *out_chunk_range = sc.chunkRange;
    return 1;
}

/* Algorithme de position GPU d'une structure :
 *   0=FEATURE, 1=LARGE, 2=OUTPOST, 3=END_CITY, -1=non supporté (CPU gère). */
EXPORT int ssa_struct_gpu_algo(int struct_type) {
    switch (struct_type) {
        case Feature: case Desert_Pyramid: case Jungle_Pyramid:
        case Swamp_Hut: case Igloo: case Village: case Ocean_Ruin:
        case Shipwreck: case Ruined_Portal: case Ruined_Portal_N:
        case Ancient_City: case Trail_Ruins: case Trial_Chambers:
        case Abandoned_Camp:
            return 0;
        case Monument: case Mansion:
            return 1;
        case Outpost:
            return 2;
        case End_City:
            return 3;
        default:
            return -1;
    }
}
EXPORT int ssa_ctype_structure_nether(void)   { return CTYPE_STRUCTURE_NETHER; }
EXPORT int ssa_ctype_structure_end(void)      { return CTYPE_STRUCTURE_END; }
EXPORT int ssa_ctype_mineshaft(void)          { return CTYPE_MINESHAFT; }
EXPORT int ssa_ctype_slime(void)              { return CTYPE_SLIME; }
EXPORT int ssa_ctype_spawn(void)              { return CTYPE_SPAWN; }
EXPORT int ssa_ctype_biome_filter(void)       { return CTYPE_BIOME_FILTER; }
EXPORT int ssa_ctype_biome_sample(void)       { return CTYPE_BIOME_SAMPLE; }
EXPORT int ssa_ctype_biome_center(void)       { return CTYPE_BIOME_CENTER; }
EXPORT int ssa_ctype_climate_minmax(void)     { return CTYPE_CLIMATE_MINMAX; }
EXPORT int ssa_ctype_climate_area(void)       { return CTYPE_CLIMATE_AREA; }
EXPORT int ssa_ctype_crunchiness(void)        { return CTYPE_CRUNCHINESS; }
EXPORT int ssa_ctype_climate_noise(void)      { return CTYPE_CLIMATE_NOISE; }
EXPORT int ssa_ctype_noise_sample(void)       { return CTYPE_NOISE_SAMPLE; }
EXPORT int ssa_ctype_height(void)             { return CTYPE_HEIGHT; }
EXPORT int ssa_ctype_logic_or(void)           { return CTYPE_LOGIC_OR; }
EXPORT int ssa_ctype_logic_not(void)          { return CTYPE_LOGIC_NOT; }

EXPORT int ssa_variant_abandoned(void)        { return VARIANT_ABANDONED; }
EXPORT int ssa_variant_not_abandoned(void)    { return VARIANT_NOT_ABANDONED; }
EXPORT int ssa_variant_basement(void)         { return VARIANT_BASEMENT; }
EXPORT int ssa_variant_no_basement(void)      { return VARIANT_NO_BASEMENT; }

/* Teste si le village a la position (x,z) est abandonne (zombie) pour cette
 * seed. Fait le biome check (pour obtenir le bon biome_id) puis getVariant.
 * Retourne 1 si abandonne, 0 sinon. Utilise par l'affichage carte. */
EXPORT int ssa_village_abandoned(int mc, int large, uint64_t seed, int x, int z)
{
    Generator g;
    setupGenerator(&g, mc, large ? LARGE_BIOMES : 0);
    applySeed(&g, DIM_OVERWORLD, seed);
    int biome_id = isViableStructurePos(Village, &g, x, z, 0);
    if (!biome_id) return 0;
    StructureVariant sv;
    memset(&sv, 0, sizeof(sv));
    getVariant(&sv, Village, mc, seed, x, z, biome_id);
    return sv.abandoned ? 1 : 0;
}

/* ───────────────────────────────────────────────────────────────────────────
 * ssa_test_seeds_batch — test CPU complet d'un LOT de seeds (mode GPU).
 *
 * Utilisé par le pipeline GPU : le GPU pré-filtre les seeds sur les positions
 * de structures (48 bits), puis ce point d'entrée teste les survivantes avec
 * l'arbre COMPLET (biomes, climats, height, spawn…), exactement comme le worker
 * de recherche. Les seeds qui passent TOUT sont écrites dans out_seeds.
 *
 * Mono-thread (le parallélisme massif est déjà fait par le GPU en amont ; ici on
 * ne traite qu'une fraction des seeds). Renvoie le nombre de seeds retenues.
 * ─────────────────────────────────────────────────────────────────────────── */
static int64_t test_seeds_batch_impl(
        const SsaCond *conds, int n_conds, int mc, int large,
        const int64_t *in_seeds, int64_t n_in,
        int64_t *out_seeds, int64_t out_cap, SsaProf *prof);

EXPORT int64_t ssa_test_seeds_batch(
        const SsaCond *conds, int n_conds, int mc, int large,
        const int64_t *in_seeds, int64_t n_in,
        int64_t *out_seeds, int64_t out_cap) {
    return test_seeds_batch_impl(conds, n_conds, mc, large, in_seeds, n_in,
                                 out_seeds, out_cap, NULL);
}

/* Same, with the per-condition statistics of the search engine (01/10): the
 * GPU pipelines validate their survivors here, and their sessions had no stats.
 * prof_out = 5 * n_conds + 1 int64 counters, ADDED to (atomically: several
 * validator threads share one buffer) — per condition: seeds reached, branch
 * passed, own ticks (sampled 1 seed in 32), checks, checks passed; then the
 * ticks of the whole seed tests. Same counting as ssa_engine_profile2. */
EXPORT int64_t ssa_test_seeds_batch_prof(
        const SsaCond *conds, int n_conds, int mc, int large,
        const int64_t *in_seeds, int64_t n_in,
        int64_t *out_seeds, int64_t out_cap, int64_t *prof_out) {
    if (!prof_out)
        return test_seeds_batch_impl(conds, n_conds, mc, large, in_seeds, n_in,
                                     out_seeds, out_cap, NULL);
    SsaProf *pf = (SsaProf *)calloc(1, sizeof(SsaProf));
    int64_t kept = test_seeds_batch_impl(conds, n_conds, mc, large, in_seeds, n_in,
                                         out_seeds, out_cap, pf);
    if (pf) {
        int n = n_conds < MAX_CONDS ? n_conds : MAX_CONDS;
        for (int i = 0; i < n; i++) {
            __atomic_fetch_add(&prof_out[5 * i],     pf->reached[i],   __ATOMIC_RELAXED);
            __atomic_fetch_add(&prof_out[5 * i + 1], pf->passed[i],    __ATOMIC_RELAXED);
            __atomic_fetch_add(&prof_out[5 * i + 2], (int64_t)pf->ticks[i], __ATOMIC_RELAXED);
            __atomic_fetch_add(&prof_out[5 * i + 3], pf->checks[i],    __ATOMIC_RELAXED);
            __atomic_fetch_add(&prof_out[5 * i + 4], pf->checks_ok[i], __ATOMIC_RELAXED);
        }
        __atomic_fetch_add(&prof_out[5 * n], (int64_t)pf->total_ticks, __ATOMIC_RELAXED);
        free(pf);
    }
    return kept;
}

static int64_t test_seeds_batch_impl(
        const SsaCond *conds, int n_conds, int mc, int large,
        const int64_t *in_seeds, int64_t n_in,
        int64_t *out_seeds, int64_t out_cap, SsaProf *prof) {
    if (n_conds <= 0 || n_conds > MAX_CONDS || n_in <= 0) return 0;

    /* Ordre d'évaluation des enfants, propre à CET appel (thread-safe) */
    ChildOrderCache *co = child_order_new(conds, n_conds);
    if (!co) return 0;

    /* toplevel_order trié cheap-first (copie de la logique de create) */
    int toplevel_order[MAX_CONDS]; int n_toplevel = 0;
    {
        int idxs[MAX_CONDS]; int64_t costs[MAX_CONDS]; int n_top = 0;
        for (int i = 0; i < n_conds && n_top < MAX_CONDS; i++) {
            if (conds[i].parent_idx == 0 && conds[i].cond_idx > 0) {
                idxs[n_top]  = i;
                costs[n_top] = cond_subtree_cost(conds, n_conds, conds[i].cond_idx);
                n_top++;
            }
        }
        for (int a = 1; a < n_top; a++) {
            int idx_a = idxs[a]; int64_t cost_a = costs[a]; int b = a - 1;
            while (b >= 0 && costs[b] > cost_a) {
                idxs[b+1] = idxs[b]; costs[b+1] = costs[b]; b--;
            }
            idxs[b+1] = idx_a; costs[b+1] = cost_a;
        }
        for (int i = 0; i < n_top; i++) toplevel_order[i] = idxs[i];
        n_toplevel = n_top;
    }

    /* Env identique au worker */
    SsaEnv env;
    memset(&env, 0, sizeof(env));
    env.co = co;
    env.prof = prof;                    /* per-condition stats (NULL = off) */
    env.mc = mc; env.large = large;
    env.current_dim = -2;
    env.gbn_seed = ~0ULL; env.gbn_nmax = -999;
    for (int i = 0; i < NP_MAX; i++)
        for (int s = 0; s < CLIM_NSLOT; s++) {
            env.climate_cache_nmax[i][s] = -2;
            env.climate_cache_seed[i][s] = 0xdeadbeefcafebabeULL; env.clazy[i].valid = 0;
        }
    uint32_t flags = large ? LARGE_BIOMES : 0;
    setupGenerator(&env.g, mc, flags);

    CondCache caches[MAX_CONDS];
    memset(caches, 0, sizeof(caches));

    int64_t kept = 0;
    for (int64_t i = 0; i < n_in; i++) {
        env.seed = (uint64_t)in_seeds[i];
        env.sn_ow_ready = 0;
        env.sn_end_ready = 0;
        /* BUG fixed 26/09: the generator must be re-seeded for EVERY seed.
         * Without this reset env_set_dim() saw "same dimension" and kept the
         * biomes of the FIRST seed of the batch → every biome / viability test
         * of the GPU-survivor validation used the wrong world. */
        env.current_dim = -2;
        prof_seed_begin(prof);
        /* every seed is timed here (02/10): the counter restarts at each call and
         * the GPU validators call with a few survivors at a time, so the engine's
         * « 1 seed in 32 » sampling never fired → no time at all (« — » in the
         * stats). The survivors are few and costly: timing them all is free. */
        if (prof) prof->timing = 1;
        uint64_t pt0 = (prof && prof->timing) ? SSA_TICKS() : 0;
        int ok = ssa_test_seed_sorted(&env, conds, n_conds, caches,
                                      toplevel_order, n_toplevel);
        if (prof && prof->timing) prof->total_ticks += SSA_TICKS() - pt0;
        if (ok) {
            if (kept < out_cap) out_seeds[kept] = in_seeds[i];
            kept++;
        }
    }
    free(co);
    return kept;
}

/* ───────────────────────────────────────────────────────────────────────────
 * ssa_explain_seed — WHERE does one seed match? (27/09)
 *
 * Evaluates the tree exactly like the search (same engine, so the same answer
 * and the same positions as the result: Monte-Carlo noise samples, spirals,
 * scale-to-dimension…) and returns, for every condition (cond_idx k → slot
 * k-1), the position where it was satisfied on the successful path.
 * out_valid[k-1] = 1 when a position is known. Returns 1 if the seed matches.
 * (Before, the UI re-evaluated with the Python evaluator, which differs on
 * Monte-Carlo noise samples: far results showed no location at all.)
 * ─────────────────────────────────────────────────────────────────────────── */
static int explain_impl(const SsaCond *conds, int n_conds, int mc, int large, int64_t seed,
                        int32_t *out_xz, uint8_t *out_valid,
                        uint8_t *out_reached, uint8_t *out_passed);

EXPORT int ssa_explain_seed(
        const SsaCond *conds, int n_conds, int mc, int large, int64_t seed,
        int32_t *out_xz, uint8_t *out_valid) {
    return explain_impl(conds, n_conds, mc, large, seed, out_xz, out_valid, NULL, NULL);
}

/* ssa_eval_single — ONE condition (conds[0], cond_idx 1, no children) of a
 * seed, evaluated at a given parent position with exactly the search's code.
 * The Python evaluator calls it for the conditions it cannot reproduce
 * bit for bit (Monte-Carlo noise samples: the C draws the same random
 * sample order as cubiomes-viewer). Returns 1 if the condition passes and
 * writes the position it hands to its children in out_xz[0..1]. */
EXPORT int ssa_eval_single(const SsaCond *conds, int mc, int large, int64_t seed,
                           int32_t at_x, int32_t at_z, int32_t *out_xz,
                           int32_t *samples_xz, int samples_cap, int *n_samples)
{
    if (!conds) return 0;
    ChildOrderCache *co = child_order_new(conds, 1);
    if (!co) return 0;
    SsaEnv env;
    memset(&env, 0, sizeof(env));
    env.co = co;
    env.mc = mc; env.large = large;
    env.current_dim = -2;
    env.gbn_seed = ~0ULL; env.gbn_nmax = -999;
    for (int i = 0; i < NP_MAX; i++)
        for (int k = 0; k < CLIM_NSLOT; k++) {
            env.climate_cache_nmax[i][k] = -2;
            env.climate_cache_seed[i][k] = 0xdeadbeefcafebabeULL; env.clazy[i].valid = 0;
        }
    setupGenerator(&env.g, mc, large ? LARGE_BIOMES : 0);
    env.seed = (uint64_t)seed;
    env_reset_positions_n(&env, 1);
    env_push(&env, 0, at_x, at_z);
    CondCache caches[1];
    memset(caches, 0, sizeof(caches));
    /* « yield individual samples »: the accepted sample positions, in the
     * search's order (its children are tried around each of them) */
    Pos *inst = NULL;
    if (n_samples) *n_samples = 0;
    if (samples_xz && samples_cap > 0) {
        inst = (Pos *)malloc(sizeof(Pos) * (size_t)samples_cap);
        if (inst) { env.sample_out = inst; env.sample_cap = samples_cap; env.sample_n = 0; }
    }
    int ok = eval_condition(&env, &conds[0], 1, at_x, at_z, conds, 1, caches);
    env.sample_out = NULL;
    if (inst) {
        int n = env.sample_n < samples_cap ? env.sample_n : samples_cap;
        for (int k = 0; k < n; k++) { samples_xz[2*k] = inst[k].x; samples_xz[2*k+1] = inst[k].z; }
        if (n_samples) *n_samples = n;
        free(inst);
    }
    if (ok && out_xz) {
        if (env.positions[1].valid) { out_xz[0] = env.positions[1].x; out_xz[1] = env.positions[1].z; }
        else { out_xz[0] = at_x; out_xz[1] = at_z; }
    }
    free(co);
    return ok ? 1 : 0;
}

/* Same, plus for every condition whether it was reached and whether its
 * branch passed (at least once) for this seed: shows WHERE a seed fails. */
EXPORT int ssa_explain_seed2(
        const SsaCond *conds, int n_conds, int mc, int large, int64_t seed,
        int32_t *out_xz, uint8_t *out_valid, uint8_t *out_reached, uint8_t *out_passed) {
    return explain_impl(conds, n_conds, mc, large, seed, out_xz, out_valid, out_reached, out_passed);
}

static int explain_impl(const SsaCond *conds, int n_conds, int mc, int large, int64_t seed,
                        int32_t *out_xz, uint8_t *out_valid,
                        uint8_t *out_reached, uint8_t *out_passed) {
    if (n_conds <= 0 || n_conds > MAX_CONDS) return 0;
    ChildOrderCache *co = child_order_new(conds, n_conds);
    if (!co) return 0;
    SsaEnv *env = (SsaEnv *)calloc(1, sizeof(SsaEnv));      /* big: not on the stack */
    CondCache *caches = (CondCache *)calloc(MAX_CONDS, sizeof(CondCache));
    if (!env || !caches) { free(co); free(env); free(caches); return 0; }
    env->co = co;
    env->mc = mc; env->large = large;
    env->current_dim = -2;
    env->gbn_seed = ~0ULL; env->gbn_nmax = -999;
    for (int i = 0; i < NP_MAX; i++)
        for (int s = 0; s < CLIM_NSLOT; s++) {
            env->climate_cache_nmax[i][s] = -2;
            env->climate_cache_seed[i][s] = 0xdeadbeefcafebabeULL; env->clazy[i].valid = 0;
        }
    setupGenerator(&env->g, mc, large ? LARGE_BIOMES : 0);
    env->seed = (uint64_t)seed;
    env->sn_ow_ready = 0; env->sn_end_ready = 0;
    for (int k = 0; k < n_conds; k++) { out_valid[k] = 0; out_xz[2*k] = out_xz[2*k+1] = 0; }
    SsaProf *pf = NULL;
    if (out_reached && out_passed) {
        pf = (SsaProf *)calloc(1, sizeof(SsaProf));
        env->prof = pf;
        prof_seed_begin(pf);
        if (pf) pf->timing = 0;
    }
    int ok = 1;
    for (int i = 0; i < n_conds; i++) {
        if (conds[i].parent_idx != 0 || conds[i].cond_idx <= 0) continue;
        env_reset_positions_n(env, n_conds);
        int r = eval_node(env, conds, n_conds, caches, conds[i].cond_idx, 0, 0);
        if (!r) { ok = 0; continue; }
        for (int k = 1; k <= n_conds && k < MAX_CONDS; k++) {
            if (env->positions[k].valid) {
                out_valid[k-1] = 1;
                out_xz[2*(k-1)]   = env->positions[k].x;
                out_xz[2*(k-1)+1] = env->positions[k].z;
            }
        }
    }
    if (pf) {
        for (int k = 0; k < n_conds; k++) {
            out_reached[k] = pf->reached[k] > 0;
            out_passed[k] = pf->passed[k] > 0;
        }
        free(pf);
    }
    free(caches); free(env); free(co);
    return ok;
}

/* ───────────────────────────────────────────────────────────────────────────
 * ssa_expand_lows — expansion BLOCKS d'une liste de lows 48-bit (mode GPU).
 *
 * Pour chaque low fourni (déjà pré-filtré sur les structures par le GPU), teste
 * les 65536 valeurs de bits hauts avec l'arbre COMPLET (biomes + tout), et écrit
 * les seeds 64-bit qui passent. C'est EXACTEMENT la 2e moitié du mode BLOCKS du
 * worker, isolée pour le pipeline GPU : GPU pré-filtre les lows → ce point
 * d'entrée fait l'expansion + validation complète sur les survivants.
 *
 * Renvoie le nombre de seeds 64-bit retenues (peut dépasser out_cap).
 * ─────────────────────────────────────────────────────────────────────────── */
EXPORT int64_t ssa_expand_lows(
        const SsaCond *conds, int n_conds, int mc, int large,
        const int64_t *in_lows, int64_t n_lows,
        int64_t *out_seeds, int64_t out_cap, int n_threads,
        volatile int *stop_flag) {
    if (n_conds <= 0 || n_conds > MAX_CONDS || n_lows <= 0) return 0;
#ifdef _OPENMP
    if (n_threads > 0) omp_set_num_threads(n_threads);
#endif

    ChildOrderCache *co = child_order_new(conds, n_conds);
    if (!co) return 0;

    int toplevel_order[MAX_CONDS]; int n_toplevel = 0;
    {
        int idxs[MAX_CONDS]; int64_t costs[MAX_CONDS]; int n_top = 0;
        for (int i = 0; i < n_conds && n_top < MAX_CONDS; i++)
            if (conds[i].parent_idx == 0 && conds[i].cond_idx > 0) {
                idxs[n_top] = i; costs[n_top] = cond_subtree_cost(conds, n_conds, conds[i].cond_idx); n_top++;
            }
        for (int a = 1; a < n_top; a++) {
            int ia = idxs[a]; int64_t ca = costs[a]; int b = a - 1;
            while (b >= 0 && costs[b] > ca) { idxs[b+1]=idxs[b]; costs[b+1]=costs[b]; b--; }
            idxs[b+1] = ia; costs[b+1] = ca;
        }
        for (int i = 0; i < n_top; i++) toplevel_order[i] = idxs[i];
        n_toplevel = n_top;
    }

    uint32_t flags = large ? LARGE_BIOMES : 0;

    int64_t kept = 0;
    /* Parallélisation OpenMP sur les lows : chaque low (et ses 65536 sister-
     * seeds) est indépendant. Le mode GPU appelait cette fonction en mono-thread
     * → CPU à ~10% (1 cœur sur 12). Chaque thread a son PROPRE env + caches +
     * generator (état mutable, ne peut pas être partagé).
     *
     * IMPORTANT : on parallélise sur l'espace COMBINÉ (li × 65536 + h), pas
     * seulement sur li. Sinon, quand le filtre GPU est très sélectif et ne laisse
     * qu'1 ou 2 lows survivants par batch, seuls 1-2 threads travaillaient (les
     * 65536 variantes d'un low étaient faites par UN seul thread) → CPU bloqué à
     * ~8%. En répartissant les (lows × 65536) graines sur tous les threads, les
     * 12 cœurs sont saturés même avec un seul low survivant.
     *
     * Les résultats sont collectés par thread puis fusionnés (pas de section
     * critique par graine, qui sérialisait et coûtait cher). */
    int64_t total_work = n_lows * 0x10000LL;

    #pragma omp parallel
    {
        SsaEnv env;
        memset(&env, 0, sizeof(env));
        env.co = co;
        env.mc = mc; env.large = large; env.current_dim = -2;
        env.gbn_seed = ~0ULL; env.gbn_nmax = -999;
        for (int i = 0; i < NP_MAX; i++)
            for (int s = 0; s < CLIM_NSLOT; s++) {
                env.climate_cache_nmax[i][s] = -2;
                env.climate_cache_seed[i][s] = 0xdeadbeefcafebabeULL; env.clazy[i].valid = 0;
            }
        setupGenerator(&env.g, mc, flags);

        CondCache caches[MAX_CONDS];
        memset(caches, 0, sizeof(caches));

        /* Buffer local au thread : evite une section critique par graine. */
        int64_t local_cap = 4096;
        int64_t *local_hits = (int64_t*)malloc(local_cap * sizeof(int64_t));
        int64_t local_n = 0;

        #pragma omp for schedule(dynamic, 4096)
        for (int64_t w = 0; w < total_work; w++) {
            /* Interruption rapide : si le flag est leve, on saute le travail.
             * Teste a CHAQUE iteration (lecture d'un int volatile, negligeable). */
            if (stop_flag && *stop_flag) continue;
            int64_t li = w >> 16;            /* indice du low */
            int64_t h  = w & 0xFFFFLL;        /* variante de bits hauts */
            uint64_t low = (uint64_t)in_lows[li] & 0xFFFFFFFFFFFFULL;
            uint64_t full = ((uint64_t)h << 48) | low;
            env.seed = full;
            env.sn_ow_ready = 0; env.sn_end_ready = 0;
            env.current_dim = -2;
            applySeed(&env.g, DIM_OVERWORLD, full);
            env.current_dim = DIM_OVERWORLD;
            if (ssa_test_seed_sorted(&env, conds, n_conds, caches,
                                      toplevel_order, n_toplevel)) {
                if (local_hits && local_n < local_cap) {
                    local_hits[local_n++] = (int64_t)full;
                } else if (local_hits) {
                    int64_t ncap = local_cap * 2;
                    int64_t *tmp = (int64_t*)realloc(local_hits, ncap * sizeof(int64_t));
                    if (tmp) { local_hits = tmp; local_cap = ncap; local_hits[local_n++] = (int64_t)full; }
                }
            }
        }

        /* Fusion des resultats locaux (1 seule section critique par thread). */
        if (local_n > 0) {
            #pragma omp critical
            {
                for (int64_t k = 0; k < local_n; k++) {
                    if (kept < out_cap) out_seeds[kept] = local_hits[k];
                    kept++;
                }
            }
        }
        if (local_hits) free(local_hits);
    }

    free(co);
    return kept;
}

