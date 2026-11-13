// tools/loot/portal_frame_dist.c — (07/11) the (need, crying) distribution of a ruined portal
// frame over the crying patterns of its obsidian (build_portal_stats.py compiles it).
// stdin: « n S R [N seed] », then R lines « m mask ».
//   n obsidian (not eaten), S = those at a ground spot of the netherrack spread (a crying
//   one there is replaced: missing, not crying), R frames (m missing, mask = its obsidian).
//   N = 0: every pattern (2^n) → « need c k count » (k = crying obsidian in the pattern);
//   N > 0: N random patterns, each obsidian crying with P(nextFloat() < 0.15f) exactly
//   (2 516 583 / 2^24) → « need c -1 count » (the giant portals: 25 – 31 obsidian).
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#define NM 48
#define CM 32
#define PK 2516583u

static uint64_t tot[NM][CM][CM];

static inline uint64_t sm64(uint64_t *s) {
    uint64_t z = (*s += 0x9E3779B97F4A7C15ULL);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

int main(void) {
    int n, R;
    unsigned long long sv, N = 0, seed = 1;
    if (scanf("%d %llu %d", &n, &sv, &R) != 3) return 1;
    uint32_t S = (uint32_t)sv;
    int *m = malloc(sizeof(int) * R); uint32_t *mk = malloc(sizeof(uint32_t) * R);
    for (int i = 0; i < R; i++) {
        unsigned long long v;
        if (scanf("%d %llu", &m[i], &v) != 2) return 1;
        mk[i] = (uint32_t)v;
    }
    if (scanf("%llu %llu", &N, &seed) != 2) { N = 0; }
    memset(tot, 0, sizeof tot);
    int64_t total = N ? (int64_t)N : (int64_t)(1ULL << n);
    #pragma omp parallel
    {
        static __thread uint64_t loc[NM][CM][CM];
        memset(loc, 0, sizeof loc);
        uint64_t rs = seed * 0x2545F4914F6CDD1DULL;
        #pragma omp for schedule(static)
        for (int64_t p = 0; p < total; p++) {
            uint32_t pt;
            if (N) {
                uint64_t st = rs ^ (uint64_t)p * 0xD1B54A32D192ED03ULL;
                pt = 0;
                for (int b = 0; b < n; b += 2) {      // two 24-bit draws per 64 bits
                    uint64_t r = sm64(&st);
                    if ((uint32_t)(r & 0xFFFFFF) < PK) pt |= 1u << b;
                    if (b + 1 < n && (uint32_t)((r >> 32) & 0xFFFFFF) < PK) pt |= 1u << (b + 1);
                }
            } else pt = (uint32_t)p;
            int bn = 1 << 30, bc = 0;
            for (int i = 0; i < R; i++) {
                int nd = m[i] + __builtin_popcount(pt & mk[i]);
                int c = __builtin_popcount(pt & mk[i] & ~S);
                if (nd < bn || (nd == bn && c < bc)) { bn = nd; bc = c; }
            }
            loc[bn][bc][N ? 0 : __builtin_popcount(pt)]++;
        }
        #pragma omp critical
        for (int a = 0; a < NM; a++) for (int b = 0; b < CM; b++) for (int c = 0; c < CM; c++)
            tot[a][b][c] += loc[a][b][c];
    }
    for (int a = 0; a < NM; a++) for (int b = 0; b < CM; b++) for (int c = 0; c < CM; c++)
        if (tot[a][b][c]) printf("%d %d %d %llu\n", a, b, N ? -1 : c, (unsigned long long)tot[a][b][c]);
    return 0;
}
