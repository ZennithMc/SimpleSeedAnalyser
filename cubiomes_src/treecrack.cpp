/*
 * treecrack.cpp — (05/11) the CPU side of SSA's tree cracker (treecrack.dll / .so,
 * built by tree_crack.py with g++). See treecrack_core.h.
 *
 *   tc_lattice_size      the grid of the first tree (rows = progress unit)
 *   tc_cpu_scan          filters 1-3 over rows of the grid, every CPU thread
 *   tc_exact             filter 4: the decoration start states of the candidates
 *   tc_reverse           decoration start → structure seeds (population reversal)
 *   tc_check             structure seeds → do they grow a chunk's trees?
 *   tc_trees             the trees a structure seed grows in a chunk (preview)
 *   sc_lift / sc_search  (06/11) structures → structure seed (structcrack_core.h)
 *   sc_check             structure seeds → do they place every structure entered?
 */
#include <atomic>
#include <thread>
#include <vector>
#include <mutex>
#include <algorithm>
#include "treecrack_core.h"
#include "structcrack_core.h"

#ifdef _WIN32
#define TC_EXPORT extern "C" __declspec(dllexport)
#else
#define TC_EXPORT extern "C" __attribute__((visibility("default")))
#endif

TC_EXPORT int tc_version(void) { return 1; }

TC_EXPORT void tc_lattice_size(int x, int z, int64_t *ci, int64_t *cj) {
    TcLattice L;
    tc_lattice(&L, x, z);
    *ci = L.ci;
    *cj = L.cj;
}

/* rows [row0, row0 + nrows) of the grid; candidates (state before tree 0) → out.
   *progress counts the rows done; *stop != 0 ends early. Returns the number of
   candidates found (may exceed cap: only cap are written). */
TC_EXPORT int64_t tc_cpu_scan(const TcChunk *c, int64_t row0, int64_t nrows, uint64_t *out,
                              int64_t cap, int nthreads, volatile int *stop,
                              volatile int64_t *progress) {
    TcLattice L;
    tc_lattice(&L, c->t[0].x, c->t[0].z);
    if (row0 < 0) row0 = 0;
    if (row0 + nrows > L.cj) nrows = L.cj - row0;
    if (nrows <= 0) return 0;
    if (nthreads < 1) nthreads = 1;
    std::atomic<int64_t> next(row0), found(0);
    std::mutex mu;
    TcChunk cc = *c;
    auto work = [&]() {
        std::vector<uint64_t> local;
        for (;;) {
            if (stop && *stop) break;
            int64_t gj = next.fetch_add(1);
            if (gj >= row0 + nrows) break;
            for (int64_t gi = 0; gi < L.ci; gi++) {
                uint64_t s0;
                if (tc_filter1(tc_lattice_state(&L, gi, gj), &cc, &s0) && tc_filter_window(s0, &cc))
                    local.push_back(s0);
            }
            if (progress) __atomic_add_fetch((int64_t *)progress, 1, __ATOMIC_RELAXED);
            if (local.size() > 256) {
                std::lock_guard<std::mutex> g(mu);
                for (uint64_t v : local) { int64_t k = found.fetch_add(1); if (k < cap) out[k] = v; }
                local.clear();
            }
        }
        std::lock_guard<std::mutex> g(mu);
        for (uint64_t v : local) { int64_t k = found.fetch_add(1); if (k < cap) out[k] = v; }
    };
    std::vector<std::thread> th;
    for (int i = 1; i < nthreads; i++) th.emplace_back(work);
    work();
    for (auto &t : th) t.join();
    return found.load();
}

/* filter 4 over candidates: the distinct decoration start states (raw state) */
TC_EXPORT int64_t tc_exact(const TcChunk *c, const uint64_t *s0, int64_t n, uint64_t *out,
                           int64_t cap, int nthreads) {
    std::vector<uint64_t> all;
    std::mutex mu;
    std::atomic<int64_t> next(0);
    if (nthreads < 1) nthreads = 1;
    auto work = [&]() {
        uint64_t buf[TC_WINDOW + 1];
        for (;;) {
            int64_t i = next.fetch_add(1);
            if (i >= n) break;
            int k = tc_filter_exact(s0[i], c, buf, TC_WINDOW + 1);
            if (k > TC_WINDOW + 1) k = TC_WINDOW + 1;
            std::lock_guard<std::mutex> g(mu);
            all.insert(all.end(), buf, buf + k);
        }
    };
    std::vector<std::thread> th;
    for (int i = 1; i < nthreads; i++) th.emplace_back(work);
    work();
    for (auto &t : th) t.join();
    std::sort(all.begin(), all.end());
    all.erase(std::unique(all.begin(), all.end()), all.end());
    int64_t m = (int64_t)all.size();
    for (int64_t i = 0; i < m && i < cap; i++) out[i] = all[i];
    return m;
}

/* decoration start state (raw) → structure seeds (48 bits) */
TC_EXPORT int tc_reverse(const TcChunk *c, uint64_t deco_state, uint64_t *out, int cap) {
    uint64_t P = tc_popseed_of(deco_state, c->salt);
    return tc_reverse_pop(P, (int64_t)c->cx * 16, (int64_t)c->cz * 16, out, cap);
}

/* ok[i] = 1 when structure seed ss[i] grows every tree of chunk c */
TC_EXPORT void tc_check(const TcChunk *c, const uint64_t *ss, int64_t n, uint8_t *ok, int nthreads) {
    std::atomic<int64_t> next(0);
    if (nthreads < 1) nthreads = 1;
    auto work = [&]() {
        for (;;) {
            int64_t i = next.fetch_add(1);
            if (i >= n) break;
            uint64_t P = tc_popseed(ss[i] & TC_MASK, (int64_t)c->cx * 16, (int64_t)c->cz * 16);
            ok[i] = (uint8_t)tc_replay(tc_deco_state(P, c->salt), c);
        }
    };
    std::vector<std::thread> th;
    for (int i = 1; i < nthreads; i++) th.emplace_back(work);
    work();
    for (auto &t : th) t.join();
}

/* The trees structure seed ss would grow in chunk (cx, cz) if every one grew:
   per tree 6 ints (x offset, z offset, type, height, leaves, 0). Returns the count. */
TC_EXPORT int tc_trees(uint64_t ss, int version, int biome, int cx, int cz, int salt, int *out) {
    uint64_t P = tc_popseed(ss & TC_MASK, (int64_t)cx * 16, (int64_t)cz * 16);
    uint64_t s = tc_deco_state(P, salt);
    int count = 10 + (tc_float(&s) < 0.1f ? 1 : 0);
    for (int i = 0; i < count; i++) {
        int x, z, ty, h, lv;
        s = tc_tree_info(s, version, biome, 1, &x, &z, &ty, &h, &lv);
        int *o = out + 6 * i;
        o[0] = x; o[1] = z; o[2] = ty; o[3] = h; o[4] = lv; o[5] = 0;
    }
    return count;
}

/* the fewest unexplained trees of each decoration start (see tc_replay_cost) */
TC_EXPORT void tc_cost(const TcChunk *c, const uint64_t *d, int64_t n, const uint32_t *cover, int32_t *cost) {
    for (int64_t i = 0; i < n; i++) cost[i] = tc_replay_cost(d[i], c, cover);
}

/* the same from structure seeds (the chunk's decoration start is derived) */
TC_EXPORT void tc_cost_ss(const TcChunk *c, const uint64_t *ss, int64_t n, const uint32_t *cover, int32_t *cost) {
    for (int64_t i = 0; i < n; i++) {
        uint64_t P = tc_popseed(ss[i] & TC_MASK, (int64_t)c->cx * 16, (int64_t)c->cz * 16);
        cost[i] = tc_replay_cost(tc_deco_state(P, c->salt), c, cover);
    }
}

/* (06/11) the trees structure seed ss grows in chunk (cx, cz) when tree i grew iff
   bit i of `grown`: 6 ints per tree as tc_trees (the 6th = grown). Returns the count. */
TC_EXPORT int tc_trees_mask(uint64_t ss, int version, int biome, int cx, int cz, int salt,
                            uint32_t grown, int *out) {
    uint64_t P = tc_popseed(ss & TC_MASK, (int64_t)cx * 16, (int64_t)cz * 16);
    uint64_t s = tc_deco_state(P, salt);
    int count = 10 + (tc_float(&s) < 0.1f ? 1 : 0);
    for (int i = 0; i < count; i++) {
        int x, z, ty, h, lv, g = (int)(grown >> i & 1);
        s = tc_tree_info(s, version, biome, g, &x, &z, &ty, &h, &lv);
        int *o = out + 6 * i;
        o[0] = x; o[1] = z; o[2] = ty; o[3] = h; o[4] = lv; o[5] = g;
    }
    return count;
}

/* test hooks */
TC_EXPORT uint64_t tc_t_skip(uint64_t s, int64_t n) { return tc_skip(s, n); }
TC_EXPORT uint64_t tc_t_lattice_state(int x, int z, int64_t gi, int64_t gj) {
    TcLattice L; tc_lattice(&L, x, z); return tc_lattice_state(&L, gi, gj);
}
TC_EXPORT void tc_t_lattice(int x, int z, int64_t *v) {
    TcLattice L; tc_lattice(&L, x, z);
    v[0] = L.k_int; v[1] = L.l_int; v[2] = L.i0; v[3] = L.j0; v[4] = L.ci; v[5] = L.cj;
}
TC_EXPORT int tc_t_test_tree(const TcChunk *c, int j, uint64_t s) {
    return tc_test_tree(s, &c->t[j], c->version, c->biome);
}
TC_EXPORT int tc_t_filter1(const TcChunk *c, uint64_t s1, uint64_t *s0) { return tc_filter1(s1, c, s0); }
TC_EXPORT int tc_t_window(const TcChunk *c, uint64_t s0) { return tc_filter_window(s0, c); }
TC_EXPORT int tc_t_replay(const TcChunk *c, uint64_t d) { return tc_replay(d, c); }
TC_EXPORT uint64_t tc_t_popseed(uint64_t ss, int64_t bx, int64_t bz) { return tc_popseed(ss, bx, bz); }

/* ── (06/11) structures ─────────────────────────────────────────────────────── */
TC_EXPORT int sc_version(void) { return 1; }

TC_EXPORT uint64_t sc_t_pos(const ScSet *S, int i, uint64_t ss) { return (uint64_t)sc_obs(&S->o[i], ss); }

/* the 20 low bits every liftable structure allows → lows (count returned, may exceed cap) */
TC_EXPORT int64_t sc_lift(const ScSet *S, uint32_t *lows, int64_t cap, int nthreads) {
    if (nthreads < 1) nthreads = 1;
    std::vector<std::vector<uint32_t>> part(nthreads);
    const uint32_t N = 1u << SC_LOWBITS;
    auto work = [&](int t) {
        for (uint32_t low = (uint32_t)t; low < N; low += (uint32_t)nthreads)
            if (sc_lift_low(S, low)) part[t].push_back(low);
    };
    std::vector<std::thread> th;
    for (int i = 1; i < nthreads; i++) th.emplace_back(work, i);
    work(0);
    for (auto &t : th) t.join();
    std::vector<uint32_t> all;
    for (auto &p : part) all.insert(all.end(), p.begin(), p.end());
    std::sort(all.begin(), all.end());
    for (int64_t i = 0; i < (int64_t)all.size() && i < cap; i++) lows[i] = all[i];
    return (int64_t)all.size();
}

/* every structure seed (high << 20 | low) for the given lows that places every
   structure; work in blocks of 2^24 seeds: *progress counts the blocks done
   (16 per low). Returns the count (only cap written). */
TC_EXPORT int64_t sc_search(const ScSet *S, const uint32_t *lows, int64_t nlows, uint64_t *out,
                            int64_t cap, int nthreads, volatile int *stop, volatile int64_t *progress) {
    if (nthreads < 1) nthreads = 1;
    const int HB = 48 - SC_LOWBITS;          // 28 high bits
    const int BLK = 24;                       // seeds per block: 2^24
    const int64_t per = 1LL << (HB - BLK);    // blocks per low: 16
    std::atomic<int64_t> next(0), found(0);
    std::mutex mu;
    auto work = [&]() {
        for (;;) {
            if (stop && *stop) break;
            int64_t w = next.fetch_add(1);
            if (w >= nlows * per) break;
            uint64_t low = lows[w / per];
            uint64_t h0 = (uint64_t)(w % per) << BLK;
            for (uint64_t h = h0; h < h0 + (1ULL << BLK); h++) {
                uint64_t ss = (h << SC_LOWBITS) | low;
                if (sc_test(S, ss)) {
                    int64_t k = found.fetch_add(1);
                    if (k < cap) out[k] = ss;
                }
            }
            if (progress) {
                std::lock_guard<std::mutex> g(mu);
                *progress += 1;
            }
        }
    };
    std::vector<std::thread> th;
    for (int i = 1; i < nthreads; i++) th.emplace_back(work);
    work();
    for (auto &t : th) t.join();
    return found.load();
}

/* ok[i] = 1 when ss[i] places every structure of S */
TC_EXPORT void sc_check(const ScSet *S, const uint64_t *ss, int64_t n, uint8_t *ok) {
    for (int64_t i = 0; i < n; i++) ok[i] = (uint8_t)sc_test(S, ss[i] & SC_M);
}
