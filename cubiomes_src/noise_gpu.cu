// noise_gpu.cu — CUDA host + kernels for the 1.18+ noise-biome BiomeScan prefilter.
//
//   noise_gpu_biomescan(mc, spline, spline_n, bt_steps.., bt_param.., bt_nodes..,
//                       bt_order, xs, zs, nsamp, y, allowed[4], seeds, n, out_counts)
//
// COOPERATIVE kernel (k_noisescan_coop): 1 BLOCK = 1 seed. The per-seed octave
// state (~15 KB, the permutation tables) lives in SHARED memory (≈30-cycle latency
// vs ≈400 for global), and the grid samples are split across the block's threads.
// This kills the global-memory-latency wall that capped the naive 1-thread/seed
// kernel (k_noisescan, kept for comparison). Seed-independent data (flat spline +
// biome tree) and the scratch buffers are PERSISTENT across calls (allocated once,
// cached by mc) — no per-batch cudaMalloc/upload churn.
//
// Build (ed):  nvcc -O3 -arch=sm_120 --shared -o noise_gpu.dll noise_gpu.cu
//   (arch auto-detected by noise_gpu.py; sm_120 = RTX 5080 Blackwell)
#include <cuda_runtime.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>      // malloc/free (used by the diagnostic) — needed on gcc/Linux
#include "noise_gpu_kernel.cuh"
#define SSA_UNIT "noise_gpu"
#include "ssa_cuda_common.cuh"
#include <mutex>

// One caller at a time: every export shares the persistent device buffers
// below. Two searches (or an engine analysis still finishing a measurement)
// calling at once freed / overwrote each other's buffers → "illegal memory
// access" and a dead CUDA context (seen 27/09 on an RTX 5080).
static std::mutex g_ng_lock;

// Seeds per launch never below 2 waves of blocks: the time bound (SsaChunker)
// used to shrink a launch to ONE seed on big areas — one block on an 84-SM GPU,
// 99 % idle: a GPU call of 1 000 seeds then took minutes (biome center r1000).
static int ng_min_seeds(int seeds_per_block) {
    static int sms = 0;
    if (!sms) {
        int v = 0;
        if (cudaDeviceGetAttribute(&v, cudaDevAttrMultiProcessorCount, 0) != cudaSuccess || v <= 0) v = 32;
        sms = v;
    }
    return 2 * sms * (seeds_per_block > 0 ? seeds_per_block : 1);
}

#ifdef _WIN32
  #define EXPORT extern "C" __declspec(dllexport)
#else
  #define EXPORT extern "C" __attribute__((visibility("default")))
#endif

#ifndef NG_BLOCK
#define NG_BLOCK 128         // threads per block (= per seed). tune -DNG_BLOCK=64/96/256
#endif
#define NG_CAP   (1 << 16)   // max seeds per launch (bounds scratch/grid ~ a few k blocks)

// ---------------------------------------------------------------------------
// naive kernel (1 thread = 1 seed, global-memory state) — kept for A/B testing
// ---------------------------------------------------------------------------
__global__ void k_noisescan(int mc, const SplineFlatG* sp, BiomeTreeG bt,
                            const int* xs, const int* zs, int nsamp, int y,
                            uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3,
                            const uint64_t* seeds, int n,
                            char* scratch, size_t stride, int* out)
{
    int tid = blockIdx.x * blockDim.x + threadIdx.x;
    if (tid >= n) return;
    BiomeNoiseG* bn = (BiomeNoiseG*)(scratch + (size_t)tid * stride);
    bn->mc = mc;
    uint64_t allowed[4] = { a0, a1, a2, a3 };
    out[tid] = Ng_scanSeed(bn, sp, &bt, seeds[tid], xs, zs, nsamp, y, allowed);
}

// ---------------------------------------------------------------------------
// cooperative kernel (1 block = 1 seed, state in SHARED memory)
// ---------------------------------------------------------------------------
__global__ void k_noisescan_coop(int mc, const SplineFlatG* sp, BiomeTreeG bt,
                                 const int* xs, const int* zs, int nsamp, int y,
                                 uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3,
                                 const uint64_t* seeds, int n, int* out)
{
    // shared layout: [ BiomeNoiseG bn | OctSeedG os[NG_OCT_TOTAL] ]  (one dynamic block)
    extern __shared__ char smem[];
    BiomeNoiseG* bn = (BiomeNoiseG*)smem;
    OctSeedG*    os = (OctSeedG*)(smem + sizeof(BiomeNoiseG));  // 8-aligned (sizeof bn % 8 == 0)
    int sidx = blockIdx.x;
    if (sidx >= n) return;                     // uniform across the block -> safe

    // 1) thread 0 derives the 46 octave seeds (cheap RNG chain) + climate offsets
    if (threadIdx.x == 0) { bn->mc = mc; Ng_deriveOctaves(bn, seeds[sidx], os); out[sidx] = 0; }
    __syncthreads();
    // 2) PARALLEL: the 46 expensive perlinInit shuffles, split across the block
    for (int k = threadIdx.x; k < NG_OCT_TOTAL; k += blockDim.x)
        Ng_perlinInitFrom(&bn->oct[k], &os[k]);
    __syncthreads();
    // 3) cooperative sampling, reduced via atomicAdd (no static __shared__)
    uint64_t allowed[4] = { a0, a1, a2, a3 };
    int cnt = 0;
    for (int i = threadIdx.x; i < nsamp; i += blockDim.x) {
        int b = Ng_biomeFP32(bn, sp, &bt, xs[i], y, zs[i]);
        if (b >= 0 && b < 256 && (allowed[(b >> 6) & 3] & (1ULL << (b & 63)))) cnt++;
    }
    atomicAdd(&out[sidx], cnt);
}

// ---------------------------------------------------------------------------
// BIOME-MAP kernel: for ONE seed, write the biome ID at each (xs[i],zs[i]) cell
// (scale-4 coords). Used to generate map tiles on the GPU — instant vs the CPU.
// 1 block handles NG_MAP_CPB contiguous cells: it inits the seed's biome noise
// once in shared, then samples its chunk. Re-init per block is cheap (amortized
// over NG_MAP_CPB samples). Same Ng_biomeFP32 as the scan kernel (validated).
// ---------------------------------------------------------------------------
#ifndef NG_MAP_CPB
#define NG_MAP_CPB 8192
#endif
__global__ void k_biomemap(int mc, const SplineFlatG* sp, BiomeTreeG bt,
                           const int* xs, const int* zs, int ncells, int y,
                           uint64_t seed, int* out_biomes)
{
    extern __shared__ char smem[];
    BiomeNoiseG* bn = (BiomeNoiseG*)smem;
    OctSeedG*    os = (OctSeedG*)(smem + sizeof(BiomeNoiseG));
    int c0 = blockIdx.x * NG_MAP_CPB;
    if (c0 >= ncells) return;                          // uniform across the block
    if (threadIdx.x == 0) { bn->mc = mc; Ng_deriveOctaves(bn, seed, os); }
    __syncthreads();
    for (int k = threadIdx.x; k < NG_OCT_TOTAL; k += blockDim.x)
        Ng_perlinInitFrom(&bn->oct[k], &os[k]);
    __syncthreads();
    int c1 = c0 + NG_MAP_CPB; if (c1 > ncells) c1 = ncells;
    for (int i = c0 + threadIdx.x; i < c1; i += blockDim.x)
        out_biomes[i] = Ng_biomeFP32(bn, sp, &bt, xs[i], y, zs[i]);
}

// ---------------------------------------------------------------------------
// CLIMATE kernel (1 block = 1 seed): min/max of one climate parameter over the
// grid (raw position, NO shift — matches cubiomes sampleClimatePara). Only the
// chosen parameter's octaves are perlinInit'd (the rest are skipped). Outputs
// per-seed min and max scaled x10000 (int atomics). No spline / biome tree needed.
//   para: 0=TEMP 1=HUMID 2=CONT 3=EROS 5=WEIRD
// ---------------------------------------------------------------------------
__global__ void k_climatescan_coop(int mc, int para, int nmax, int lo10k, int hi10k,
                                   const int* xs, const int* zs, int nsamp,
                                   const uint64_t* seeds, int n,
                                   int* out_min, int* out_max, int* out_count)
{
    extern __shared__ char smem[];
    BiomeNoiseG* bn = (BiomeNoiseG*)smem;
    OctSeedG*    os = (OctSeedG*)(smem + sizeof(BiomeNoiseG));
    int sidx = blockIdx.x;
    if (sidx >= n) return;

    if (threadIdx.x == 0) {
        bn->mc = mc; Ng_deriveOctaves(bn, seeds[sidx], os);
        if (nmax > 0) {        // "up to octave": re-derive this parameter with its limit
            XoroG pxr; Xg_set(&pxr, seeds[sidx]);
            uint64_t xlo = Xg_long(&pxr), xhi = Xg_long(&pxr);
            Ng_derClimate(os, bn->climate[para].octA.oct_off, &bn->climate[para], xlo, xhi, para, nmax);
        }
        out_min[sidx] = 0x7fffffff; out_max[sidx] = (int)0x80000000; out_count[sidx] = 0;
    }
    __syncthreads();
    // perlinInit ONLY the chosen parameter's octaves (octA..octB contiguous range)
    int lo = bn->climate[para].octA.oct_off;
    int hi = bn->climate[para].octB.oct_off + bn->climate[para].octB.octcnt;
    for (int k = lo + threadIdx.x; k < hi; k += blockDim.x)
        Ng_perlinInitFrom(&bn->oct[k], &os[k]);
    __syncthreads();

    int lmin = 0x7fffffff, lmax = (int)0x80000000, lcnt = 0;
    for (int i = threadIdx.x; i < nsamp; i += blockDim.x) {
        float v = Ng_dp32(bn->oct, &bn->climate[para], xs[i], 0, zs[i]);  // raw pos, no shift
        int vi = (int)(10000.0f * v);
        if (vi < lmin) lmin = vi;
        if (vi > lmax) lmax = vi;
        if (vi >= lo10k && vi <= hi10k) lcnt++;   // samples inside [lo,hi] (Climate noise samples)
    }
    atomicMin(&out_min[sidx], lmin);
    atomicMax(&out_max[sidx], lmax);
    atomicAdd(&out_count[sidx], lcnt);
}

// ---------------------------------------------------------------------------
// MULTI-SEED climate scan: S seeds per block with a COMPACT, K-strided pool.
// The plain kernel inits only K octaves (4..18) -> only K of 128 threads work
// during init. Here each block packs S seeds so all threads stay busy, and the
// per-seed pool holds just the chosen param's K octaves (not the full 46-pool).
// Bit-exact vs k_climatescan_coop (validated in C: 28400 seeds, 0 mismatch).
// shared layout per block: PerlinG pool[S*K] | DPerlinG dp[S] | OctSeedG os[S*K] | int red[3*S]
// ---------------------------------------------------------------------------
__global__ void k_climatescan_multi(int mc, int para, int nmax, int K, int S,
                                    int lo10k, int hi10k,
                                    const int* xs, const int* zs, int nsamp,
                                    const uint64_t* seeds, int n,
                                    int* out_min, int* out_max, int* out_count)
{
    extern __shared__ char smem[];
    PerlinG*  pool = (PerlinG*)smem;
    DPerlinG* dp   = (DPerlinG*)(pool + (size_t)S * K);
    OctSeedG* os   = (OctSeedG*)(dp + S);
    int*      red  = (int*)(os + (size_t)S * K);
    int base = blockIdx.x * S;
    int tid  = threadIdx.x;
    int nb   = n - base; if (nb > S) nb = S;   // valid seeds in this block
    if (nb <= 0) return;

    // 1) derive each seed's K octave seeds (compact, offset 0) then shift into its slice
    if (tid < nb) {
        XoroG pxr; Xg_set(&pxr, seeds[base + tid]);
        uint64_t xlo = Xg_long(&pxr), xhi = Xg_long(&pxr);
        Ng_derClimate(&os[tid * K], 0, &dp[tid], xlo, xhi, para, nmax);
        dp[tid].octA.oct_off += tid * K;
        dp[tid].octB.oct_off += tid * K;
        red[tid*3+0] = 0x7fffffff; red[tid*3+1] = (int)0x80000000; red[tid*3+2] = 0;
    }
    __syncthreads();
    // 2) parallel init: nb*K octave shuffles spread across ALL threads
    for (int g = tid; g < nb * K; g += blockDim.x) {
        int sd = g / K, lk = g % K;
        Ng_perlinInitFrom(&pool[sd * K + lk], &os[sd * K + lk]);
    }
    __syncthreads();
    // 3) cooperative sampling: nb*nsamp (seed,cell) pairs across ALL threads
    for (int g = tid; g < nb * nsamp; g += blockDim.x) {
        int sd = g / nsamp, ci = g % nsamp;
        float v = Ng_dp32(pool, &dp[sd], xs[ci], 0, zs[ci]);   // raw pos, no shift
        int vi = (int)(10000.0f * v);
        atomicMin(&red[sd*3+0], vi);
        atomicMax(&red[sd*3+1], vi);
        if (vi >= lo10k && vi <= hi10k) atomicAdd(&red[sd*3+2], 1);
    }
    __syncthreads();
    // 4) write per-seed results
    if (tid < nb) {
        out_min[base+tid]   = red[tid*3+0];
        out_max[base+tid]   = red[tid*3+1];
        out_count[base+tid] = red[tid*3+2];
    }
}


// ---------------------------------------------------------------------------
// FUSED climate pre-filter: same work as k_climatescan_multi, but the seeds
// are generated on the GPU (seed0 + i) and only the survivors come back
// (compacted with an atomic counter, any order). The Python round trip of the
// scan (building every seed, 3 result arrays per seed, the mask) capped the GPU
// at ~16 M seeds/s on a 1-octave point condition (27/09) — slower than the C
// engine on the whole session.
//   keep (mode 0): max >= A && min <= B      (in range somewhere)
//   keep (mode 1): min <= A || max >= B      (inverted: out of range somewhere)
// ---------------------------------------------------------------------------
__global__ void k_climatefilter_multi(int mc, int para, int nmax, int K, int S,
                                      const int* xs, const int* zs, int nsamp,
                                      uint64_t seed0, int n, int mode, long long A, long long B,
                                      uint64_t* out, int cap, unsigned int* out_n)
{
    extern __shared__ char smem[];
    PerlinG*  pool = (PerlinG*)smem;
    DPerlinG* dp   = (DPerlinG*)(pool + (size_t)S * K);
    OctSeedG* os   = (OctSeedG*)(dp + S);
    int*      red  = (int*)(os + (size_t)S * K);
    int base = blockIdx.x * S;
    int tid  = threadIdx.x;
    int nb   = n - base; if (nb > S) nb = S;
    if (nb <= 0) return;
    if (tid < nb) {
        XoroG pxr; Xg_set(&pxr, seed0 + (uint64_t)(base + tid));
        uint64_t xlo = Xg_long(&pxr), xhi = Xg_long(&pxr);
        Ng_derClimate(&os[tid * K], 0, &dp[tid], xlo, xhi, para, nmax);
        dp[tid].octA.oct_off += tid * K;
        dp[tid].octB.oct_off += tid * K;
        red[tid*2+0] = 0x7fffffff; red[tid*2+1] = (int)0x80000000;
    }
    __syncthreads();
    for (int g = tid; g < nb * K; g += blockDim.x) {
        int sd = g / K, lk = g % K;
        Ng_perlinInitFrom(&pool[sd * K + lk], &os[sd * K + lk]);
    }
    __syncthreads();
    for (int g = tid; g < nb * nsamp; g += blockDim.x) {
        int sd = g / nsamp, ci = g % nsamp;
        float v = Ng_dp32(pool, &dp[sd], xs[ci], 0, zs[ci]);
        int vi = (int)(10000.0f * v);
        atomicMin(&red[sd*2+0], vi);
        atomicMax(&red[sd*2+1], vi);
    }
    __syncthreads();
    if (tid < nb) {
        long long mn = red[tid*2+0], mx = red[tid*2+1];
        int keep = mode ? (mn <= A || mx >= B) : (mx >= A && mn <= B);
        if (keep) {
            unsigned int k = atomicAdd(out_n, 1u);
            if (k < (unsigned int)cap) out[k] = seed0 + (uint64_t)(base + tid);
        }
    }
}


// ---------------------------------------------------------------------------
// MONTE-CARLO PREFIX kernel (biome_sample), 1 block = 1 seed.
// cubiomes' monteCarloBiomes draws its sample cells with a Java RNG seeded by
// the WORLD SEED (setSeed(rng, seed); x = nextInt(sx), y = nextInt(sy),
// z = nextInt(sz)); cells outside the disk are skipped without counting. With
// no success at all it always rejects after m0 counted cells (m0 computed on the
// host). So the block walks the SAME cells as the CPU and keeps the seed as soon
// as one is a required biome (out=1); none among the first m0 → out=0: the CPU
// rejects that seed too.
// ---------------------------------------------------------------------------
__device__ __forceinline__ int ng_jnext(uint64_t* s, int bits) {
    *s = (*s * 0x5deece66dULL + 0xbULL) & ((1ULL << 48) - 1);
    return (int)((int64_t)*s >> (48 - bits));
}
__device__ __forceinline__ int ng_jnextInt(uint64_t* s, int n) {
    int bits, val; const int m = n - 1;
    if ((m & n) == 0) { uint64_t x = n * (uint64_t)ng_jnext(s, 31); return (int)((int64_t)x >> 31); }
    do { bits = ng_jnext(s, 31); val = bits % n; } while ((int32_t)((uint32_t)bits - val + m) < 0);
    return val;
}
// 1 BLOCK = 1 SEED, two phases and NO barrier inside a loop:
//   1) thread 0 derives the octaves and draws ALL the first m0 counted cells
//      (the RNG is sequential; ~1 000 cheap steps) into shared memory, as
//      16-bit offsets from (rx, rz); the block does the 46 perlin shuffles;
//   2) the block evaluates the cells in parallel (FP32 biome), a shared flag
//      stops everyone at the first hit.
// History (27/09, RTX 5080): v1 (rounds of 256 cells with __syncthreads in a
// loop) never returned once a seed needed a 2nd round; v2 (1 thread = 1 seed)
// worked but ran at ~1 300 seeds/s — 32 seeds per warp diverging for ~20 ms
// each. The logic is the one validated on the CPU (host build of the same
// device code: 400 seeds, 0 difference with the FP64 reference).
#ifndef NG_MC_MAXCELLS
#define NG_MC_MAXCELLS 4096
#endif
__global__ void k_biomemc_coop2(int mc, const SplineFlatG* sp, BiomeTreeG bt,
                                int rx, int rz, int sx, int sz, int y, long long rmax_sq,
                                int m0, int max_iter,
                                uint64_t a0, uint64_t a1, uint64_t a2, uint64_t a3,
                                const uint64_t* seeds, int n, int* out)
{
    // shared: [ bn | os[NG_OCT_TOTAL] | ctl[4] int | dx[MAXCELLS] u16 | dz[MAXCELLS] u16 ]
    extern __shared__ char smem[];
    BiomeNoiseG* bn  = (BiomeNoiseG*)smem;
    OctSeedG*    os  = (OctSeedG*)(smem + sizeof(BiomeNoiseG));
    int*         ctl = (int*)(os + NG_OCT_TOTAL);          // [0]=count [1]=hit
    uint16_t*    cxs = (uint16_t*)(ctl + 4);
    uint16_t*    czs = cxs + NG_MC_MAXCELLS;
    int sidx = blockIdx.x;
    if (sidx >= n) return;                     // uniform across the block
    if (threadIdx.x == 0) {
        bn->mc = mc; Ng_deriveOctaves(bn, seeds[sidx], os);
        uint64_t rng = (seeds[sidx] ^ 0x5deece66dULL) & ((1ULL << 48) - 1);
        int m = 0, it = 0, sure = 0;
        while (m < m0) {
            if (it >= max_iter) { sure = 1; break; }       // unsure -> keep
            int tx = ng_jnextInt(&rng, sx);
            (void)ng_jnextInt(&rng, 1);                     // y (sy == 1)
            int tz = ng_jnextInt(&rng, sz);
            it++;
            if (rmax_sq) {
                long long dx = (long long)(rx + tx) * 4, dz = (long long)(rz + tz) * 4;
                if (dx * dx + dz * dz >= rmax_sq) continue; // outside: not counted
            }
            cxs[m] = (uint16_t)tx; czs[m] = (uint16_t)tz; m++;
        }
        ctl[0] = m; ctl[1] = sure;
    }
    __syncthreads();
    for (int k = threadIdx.x; k < NG_OCT_TOTAL; k += blockDim.x)
        Ng_perlinInitFrom(&bn->oct[k], &os[k]);
    __syncthreads();
    volatile int* vhit = ctl + 1;
    int cnt = ctl[0];
    uint64_t allowed[4] = { a0, a1, a2, a3 };
    for (int i = threadIdx.x; i < cnt; i += blockDim.x) {
        if (*vhit) break;                      // another thread already found one
        int b = Ng_biomeFP32(bn, sp, &bt, rx + (int)cxs[i], y, rz + (int)czs[i]);
        if (b >= 0 && b < 256 && (allowed[(b >> 6) & 3] & (1ULL << (b & 63)))) *vhit = 1;
    }
    __syncthreads();
    if (threadIdx.x == 0) out[sidx] = *vhit ? 1 : 0;
}

// ---------------------------------------------------------------------------
// persistent device state (allocated once, tables cached by mc) — no per-call churn
// ---------------------------------------------------------------------------
static int          g_mc       = -1;
static SplineFlatG* g_sp       = nullptr;
static uint32_t*    g_steps    = nullptr;
static int32_t*     g_param    = nullptr;
static uint64_t*    g_nodes    = nullptr;
static unsigned int g_order    = 0;
static int          g_nodes_n  = 0;
static int*         g_xs       = nullptr;
static int*         g_zs       = nullptr;
static int          g_nsamp_cap = 0;
static uint64_t*    g_seeds    = nullptr;
static int*         g_out      = nullptr;
static char*        g_scratch  = nullptr;   // only used by the naive kernel path
static int*         g_omin     = nullptr;   // climate min/max/count outputs
static int*         g_omax     = nullptr;
static int*         g_ocount   = nullptr;
static int          g_cap      = 0;

EXPORT void noise_gpu_reset(void)
{
    std::lock_guard<std::mutex> _ng_guard(g_ng_lock);
    cudaFree(g_sp); cudaFree(g_steps); cudaFree(g_param); cudaFree(g_nodes);
    cudaFree(g_xs); cudaFree(g_zs); cudaFree(g_seeds); cudaFree(g_out); cudaFree(g_scratch);
    cudaFree(g_omin); cudaFree(g_omax); cudaFree(g_ocount);
    g_sp = nullptr; g_steps = nullptr; g_param = nullptr; g_nodes = nullptr;
    g_xs = nullptr; g_zs = nullptr; g_seeds = nullptr; g_out = nullptr; g_scratch = nullptr;
    g_omin = nullptr; g_omax = nullptr; g_ocount = nullptr;
    g_mc = -1; g_nodes_n = 0; g_nsamp_cap = 0; g_cap = 0; g_order = 0;
}

EXPORT int noise_gpu_biomescan(
    int mc,
    const SplineFlatG* spline, int spline_n,
    const uint32_t* bt_steps, int bt_steps_n,
    const int32_t*  bt_param, int bt_param_n,
    const uint64_t* bt_nodes, int bt_nodes_n,
    unsigned int bt_order,
    const int* xs, const int* zs, int nsamp, int y,
    const uint64_t* allowed,
    const uint64_t* seeds, int n,
    int* out_counts)
{
    std::lock_guard<std::mutex> _ng_guard(g_ng_lock);
    cudaError_t e; int rc = 0;
    // Declared BEFORE the first NG_CK: its `goto done` must not jump past an
    // initializer that is in scope at `done:` (gcc / strict nvcc reject that as
    // an error; MSVC only warns — that's why it built for one GPU but not the 1060).
    size_t stride = (sizeof(BiomeNoiseG) + 255) & ~((size_t)255);
    #define NG_CK(call) do{ e=(call); if(e!=cudaSuccess){ \
        ssa_set_error(e, #call, __LINE__); rc=-1; goto done; } }while(0)

    // recursion stack for Ng_resnode / Ng_getSpline (per thread)
    static int stack_set = 0;
    if (!stack_set) {
        NG_CK(cudaDeviceSetLimit(cudaLimitStackSize, 8*1024));
        // prefer max shared-memory carveout so more 16 KB blocks fit per SM
        cudaFuncSetAttribute(k_noisescan_coop, cudaFuncAttributePreferredSharedMemoryCarveout,
                             cudaSharedmemCarveoutMaxShared);
        stack_set = 1;
    }

    // (re)upload seed-independent tables when the version changes
    if (g_mc != mc) {
        cudaFree(g_sp); cudaFree(g_steps); cudaFree(g_param); cudaFree(g_nodes);
        g_sp = nullptr; g_steps = nullptr; g_param = nullptr; g_nodes = nullptr;
        NG_CK(cudaMalloc(&g_sp,    (size_t)spline_n  * sizeof(SplineFlatG)));
        NG_CK(cudaMalloc(&g_steps, (size_t)bt_steps_n* sizeof(uint32_t)));
        NG_CK(cudaMalloc(&g_param, (size_t)bt_param_n* sizeof(int32_t)));
        NG_CK(cudaMalloc(&g_nodes, (size_t)bt_nodes_n* sizeof(uint64_t)));
        NG_CK(cudaMemcpy(g_sp,    spline,   (size_t)spline_n  * sizeof(SplineFlatG), cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_steps, bt_steps, (size_t)bt_steps_n* sizeof(uint32_t),    cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_param, bt_param, (size_t)bt_param_n* sizeof(int32_t),     cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_nodes, bt_nodes, (size_t)bt_nodes_n* sizeof(uint64_t),    cudaMemcpyHostToDevice));
        g_nodes_n = bt_nodes_n; g_order = bt_order; g_mc = mc;
    }

    // (re)upload the sample grid when it grows / changes
    if (nsamp > g_nsamp_cap) {
        cudaFree(g_xs); cudaFree(g_zs); g_xs = nullptr; g_zs = nullptr;
        NG_CK(cudaMalloc(&g_xs, (size_t)nsamp * sizeof(int)));
        NG_CK(cudaMalloc(&g_zs, (size_t)nsamp * sizeof(int)));
        g_nsamp_cap = nsamp;
    }
    NG_CK(cudaMemcpy(g_xs, xs, (size_t)nsamp * sizeof(int), cudaMemcpyHostToDevice));
    NG_CK(cudaMemcpy(g_zs, zs, (size_t)nsamp * sizeof(int), cudaMemcpyHostToDevice));

    // ensure per-launch buffers (capacity NG_CAP), allocated once.
    // g_scratch = per-thread BiomeNoiseG pool for the naive kernel (persistent).
    // (stride is declared at the top of the function — see note there)
    if (g_cap < NG_CAP) {
        cudaFree(g_seeds); cudaFree(g_out); cudaFree(g_scratch);
        g_seeds = nullptr; g_out = nullptr; g_scratch = nullptr;
        NG_CK(cudaMalloc(&g_seeds,   (size_t)NG_CAP * sizeof(uint64_t)));
        NG_CK(cudaMalloc(&g_out,     (size_t)NG_CAP * sizeof(int)));
        NG_CK(cudaMalloc(&g_scratch, (size_t)NG_CAP * stride));
        g_cap = NG_CAP;
    }

    {
        BiomeTreeG bt;
        bt.steps = g_steps; bt.param = g_param; bt.nodes = g_nodes;
        bt.order = g_order; bt.len = (uint32_t)g_nodes_n;
        size_t shmem = sizeof(BiomeNoiseG) + NG_OCT_TOTAL * sizeof(OctSeedG);
        (void)stride;
        // Lancements bornés en durée (~SSA_TARGET_MS) : l'unité du découpage est
        // « graine × point d'échantillonnage », donc indépendante de la taille de zone.
        static SsaChunker ck(1LL << 22, 1LL << 12, 1LL << 36);
        for (int off = 0; off < n; ) {
            int64_t _c = ck.chunk / (nsamp > 0 ? nsamp : 1);
            if (_c < ng_min_seeds(1)) _c = ng_min_seeds(1);
            if (_c > NG_CAP) _c = NG_CAP;
            int cur = (n - off < _c) ? (n - off) : (int)_c;
            ck.begin();
            NG_CK(cudaMemcpy(g_seeds, seeds + off, (size_t)cur * sizeof(uint64_t), cudaMemcpyHostToDevice));
            // DIAGNOSTIC: 1 block = 1 seed, shared bn, thread 0 does the full scan.
            k_noisescan_coop<<<cur, NG_BLOCK, shmem>>>(mc, g_sp, bt, g_xs, g_zs, nsamp, y,
                allowed[0], allowed[1], allowed[2], allowed[3], g_seeds, cur, g_out);
            NG_CK(cudaGetLastError());
            NG_CK(cudaDeviceSynchronize());
            NG_CK(cudaMemcpy(out_counts + off, g_out, (size_t)cur * sizeof(int), cudaMemcpyDeviceToHost));
            ck.end();
            off += cur;
        }
    }

done:
    return rc;
    #undef NG_CK
}


// ---------------------------------------------------------------------------
// biome_sample Monte-Carlo prefix pre-filter (see k_biomemc_coop2). out_keep[i]
// = 1 if the seed may pass (the C engine then decides), 0 if the C engine
// certainly rejects it (up to the FP32 biome). Tables cached like biomescan.
// ---------------------------------------------------------------------------
EXPORT int noise_gpu_biomemc(
    int mc,
    const SplineFlatG* spline, int spline_n,
    const uint32_t* bt_steps, int bt_steps_n,
    const int32_t*  bt_param, int bt_param_n,
    const uint64_t* bt_nodes, int bt_nodes_n,
    unsigned int bt_order,
    int rx, int rz, int sx, int sz, int y, long long rmax_sq, int m0, int max_iter,
    const uint64_t* allowed,
    const uint64_t* seeds, int n,
    int* out_keep)
{
    std::lock_guard<std::mutex> _ng_guard(g_ng_lock);
    cudaError_t e; int rc = 0;
    #define NG_CK(call) do{ e=(call); if(e!=cudaSuccess){ \
        ssa_set_error(e, #call, __LINE__); rc=-1; goto done; } }while(0)
    static int mcset = 0;
    if (n <= 0) return 0;
    if (sx < 1 || sz < 1 || m0 < 1) return -2;
    if (sx > 65536 || sz > 65536 || m0 > NG_MC_MAXCELLS) return -3;   // host checks first
    if (!mcset) {
        NG_CK(cudaDeviceSetLimit(cudaLimitStackSize, 8*1024));
        mcset = 1;
    }
    if (g_mc != mc) {
        cudaFree(g_sp); cudaFree(g_steps); cudaFree(g_param); cudaFree(g_nodes);
        g_sp = nullptr; g_steps = nullptr; g_param = nullptr; g_nodes = nullptr;
        NG_CK(cudaMalloc(&g_sp,    (size_t)spline_n  * sizeof(SplineFlatG)));
        NG_CK(cudaMalloc(&g_steps, (size_t)bt_steps_n* sizeof(uint32_t)));
        NG_CK(cudaMalloc(&g_param, (size_t)bt_param_n* sizeof(int32_t)));
        NG_CK(cudaMalloc(&g_nodes, (size_t)bt_nodes_n* sizeof(uint64_t)));
        NG_CK(cudaMemcpy(g_sp,    spline,   (size_t)spline_n  * sizeof(SplineFlatG), cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_steps, bt_steps, (size_t)bt_steps_n* sizeof(uint32_t),    cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_param, bt_param, (size_t)bt_param_n* sizeof(int32_t),     cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_nodes, bt_nodes, (size_t)bt_nodes_n* sizeof(uint64_t),    cudaMemcpyHostToDevice));
        g_nodes_n = bt_nodes_n; g_order = bt_order; g_mc = mc;
    }
    // only what this kernel needs, without freeing the other exports' buffers
    // (it used to reallocate them all, plus a 1 GB scratch it does not use)
    if (!g_seeds) NG_CK(cudaMalloc(&g_seeds, (size_t)NG_CAP * sizeof(uint64_t)));
    if (!g_out)   NG_CK(cudaMalloc(&g_out,   (size_t)NG_CAP * sizeof(int)));
    {
        BiomeTreeG bt;
        bt.steps = g_steps; bt.param = g_param; bt.nodes = g_nodes;
        bt.order = g_order; bt.len = (uint32_t)g_nodes_n;
        size_t shmem = sizeof(BiomeNoiseG) + NG_OCT_TOTAL * sizeof(OctSeedG)
                     + 4 * sizeof(int) + 2 * NG_MC_MAXCELLS * sizeof(uint16_t);
        if (shmem > 48 * 1024)
            NG_CK(cudaFuncSetAttribute(k_biomemc_coop2,
                  cudaFuncAttributeMaxDynamicSharedMemorySize, (int)shmem));
        // launches bounded in time; unit = seed (1 block each), never below
        // 256 seeds per launch (the per-launch cost is not proportional)
        static SsaChunker ck(4096, 256, NG_CAP);
        for (int off = 0; off < n; ) {
            int64_t _c = ck.chunk;
            if (_c < ng_min_seeds(1)) _c = ng_min_seeds(1);
            if (_c > NG_CAP) _c = NG_CAP;
            int cur = (n - off < _c) ? (n - off) : (int)_c;
            ck.begin();
            NG_CK(cudaMemcpy(g_seeds, seeds + off, (size_t)cur * sizeof(uint64_t), cudaMemcpyHostToDevice));
            k_biomemc_coop2<<<cur, NG_BLOCK, shmem>>>(mc, g_sp, bt, rx, rz, sx, sz, y, rmax_sq,
                m0, max_iter, allowed[0], allowed[1], allowed[2], allowed[3], g_seeds, cur, g_out);
            NG_CK(cudaGetLastError());
            NG_CK(cudaDeviceSynchronize());
            NG_CK(cudaMemcpy(out_keep + off, g_out, (size_t)cur * sizeof(int), cudaMemcpyDeviceToHost));
            ck.end();
            off += cur;
        }
    }
done:
    return rc;
    #undef NG_CK
}

// ---------------------------------------------------------------------------
// BIOME-MAP: for ONE seed, return the biome ID at each (xs[i],zs[i]) cell (the
// xs/zs are scale-4 coords = world>>2, same convention as biomescan). Generates
// map tiles on the GPU — instant. Reuses the cached tables (g_*) uploaded by
// biomescan/this call. Output buffer is persistent (grows as needed).
// ---------------------------------------------------------------------------
EXPORT int noise_gpu_biomemap(
    int mc,
    const SplineFlatG* spline, int spline_n,
    const uint32_t* bt_steps, int bt_steps_n,
    const int32_t*  bt_param, int bt_param_n,
    const uint64_t* bt_nodes, int bt_nodes_n,
    unsigned int bt_order,
    const int* xs, const int* zs, int ncells, int y,
    uint64_t seed, int* out_biomes)
{
    std::lock_guard<std::mutex> _ng_guard(g_ng_lock);
    cudaError_t e; int rc = 0;
    static int* mapout = nullptr;
    static int  mapcap = 0;
    static int  mapset = 0;
    #define NG_CK(call) do{ e=(call); if(e!=cudaSuccess){ \
        ssa_set_error(e, #call, __LINE__); rc=-1; goto done; } }while(0)

    if (ncells <= 0) return 0;
    if (!mapset) {
        NG_CK(cudaDeviceSetLimit(cudaLimitStackSize, 8*1024));
        cudaFuncSetAttribute(k_biomemap, cudaFuncAttributePreferredSharedMemoryCarveout,
                             cudaSharedmemCarveoutMaxShared);
        mapset = 1;
    }
    // (re)upload seed-independent tables when the version changes (shared with biomescan)
    if (g_mc != mc) {
        cudaFree(g_sp); cudaFree(g_steps); cudaFree(g_param); cudaFree(g_nodes);
        g_sp = nullptr; g_steps = nullptr; g_param = nullptr; g_nodes = nullptr;
        NG_CK(cudaMalloc(&g_sp,    (size_t)spline_n  * sizeof(SplineFlatG)));
        NG_CK(cudaMalloc(&g_steps, (size_t)bt_steps_n* sizeof(uint32_t)));
        NG_CK(cudaMalloc(&g_param, (size_t)bt_param_n* sizeof(int32_t)));
        NG_CK(cudaMalloc(&g_nodes, (size_t)bt_nodes_n* sizeof(uint64_t)));
        NG_CK(cudaMemcpy(g_sp,    spline,   (size_t)spline_n  * sizeof(SplineFlatG), cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_steps, bt_steps, (size_t)bt_steps_n* sizeof(uint32_t),    cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_param, bt_param, (size_t)bt_param_n* sizeof(int32_t),     cudaMemcpyHostToDevice));
        NG_CK(cudaMemcpy(g_nodes, bt_nodes, (size_t)bt_nodes_n* sizeof(uint64_t),    cudaMemcpyHostToDevice));
        g_nodes_n = bt_nodes_n; g_order = bt_order; g_mc = mc;
    }
    // grid + output buffers (grow as needed)
    if (ncells > g_nsamp_cap) {
        cudaFree(g_xs); cudaFree(g_zs); g_xs = nullptr; g_zs = nullptr;
        NG_CK(cudaMalloc(&g_xs, (size_t)ncells * sizeof(int)));
        NG_CK(cudaMalloc(&g_zs, (size_t)ncells * sizeof(int)));
        g_nsamp_cap = ncells;
    }
    if (ncells > mapcap) {
        cudaFree(mapout); mapout = nullptr;
        NG_CK(cudaMalloc(&mapout, (size_t)ncells * sizeof(int)));
        mapcap = ncells;
    }
    NG_CK(cudaMemcpy(g_xs, xs, (size_t)ncells * sizeof(int), cudaMemcpyHostToDevice));
    NG_CK(cudaMemcpy(g_zs, zs, (size_t)ncells * sizeof(int), cudaMemcpyHostToDevice));
    {
        BiomeTreeG bt;
        bt.steps = g_steps; bt.param = g_param; bt.nodes = g_nodes;
        bt.order = g_order; bt.len = (uint32_t)g_nodes_n;
        size_t shmem = sizeof(BiomeNoiseG) + NG_OCT_TOTAL * sizeof(OctSeedG);
        int nblocks = (ncells + NG_MAP_CPB - 1) / NG_MAP_CPB;
        k_biomemap<<<nblocks, NG_BLOCK, shmem>>>(mc, g_sp, bt, g_xs, g_zs, ncells, y,
                                                 seed, mapout);
        NG_CK(cudaGetLastError());
        NG_CK(cudaDeviceSynchronize());
        NG_CK(cudaMemcpy(out_biomes, mapout, (size_t)ncells * sizeof(int), cudaMemcpyDeviceToHost));
    }
done:
    return rc;
    #undef NG_CK
}

// ---------------------------------------------------------------------------
// CLIMATE min/max scan: per-seed min and max of one climate parameter over the
// grid (raw position, no shift). Outputs scaled x10000 (int). No tables needed.
static int climatescan_impl(
    int mc, int para, int nmax, int lo10k, int hi10k,
    const int* xs, const int* zs, int nsamp,
    const uint64_t* seeds, int n,
    int* out_min, int* out_max, int* out_count)
{
    std::lock_guard<std::mutex> _ng_guard(g_ng_lock);
    cudaError_t e; int rc = 0;
    #define NG_CK(call) do{ e=(call); if(e!=cudaSuccess){ \
        ssa_set_error(e, #call, __LINE__); rc=-1; goto done; } }while(0)

    static int cset = 0;
    static int g_maxshm = 48 * 1024;   // device shared-mem-per-block budget (Pascal=48K)
    if (!cset) {
        NG_CK(cudaDeviceSetLimit(cudaLimitStackSize, 8*1024));
        cudaFuncSetAttribute(k_climatescan_coop, cudaFuncAttributePreferredSharedMemoryCarveout,
                             cudaSharedmemCarveoutMaxShared);
        // Query the REAL shared-memory limit (Pascal/GTX 10xx = 48 KB, Volta+ up to
        // 96-228 KB). Hardcoding 96 KB made the multi-seed launch fail ("invalid
        // argument") on a GTX 1060. Size S to this below.
        int ms = 0;
        if (cudaDeviceGetAttribute(&ms, cudaDevAttrMaxSharedMemoryPerBlockOptin, 0) == cudaSuccess && ms > 0)
            g_maxshm = ms;
        else if (cudaDeviceGetAttribute(&ms, cudaDevAttrMaxSharedMemoryPerBlock, 0) == cudaSuccess && ms > 0)
            g_maxshm = ms;
        cudaFuncSetAttribute(k_climatescan_multi, cudaFuncAttributeMaxDynamicSharedMemorySize, g_maxshm);
        cudaFuncSetAttribute(k_climatescan_multi, cudaFuncAttributePreferredSharedMemoryCarveout,
                             cudaSharedmemCarveoutMaxShared);
        cset = 1;
    }
    if (nsamp > g_nsamp_cap) {
        cudaFree(g_xs); cudaFree(g_zs); g_xs = nullptr; g_zs = nullptr;
        NG_CK(cudaMalloc(&g_xs, (size_t)nsamp * sizeof(int)));
        NG_CK(cudaMalloc(&g_zs, (size_t)nsamp * sizeof(int)));
        g_nsamp_cap = nsamp;
    }
    NG_CK(cudaMemcpy(g_xs, xs, (size_t)nsamp * sizeof(int), cudaMemcpyHostToDevice));
    NG_CK(cudaMemcpy(g_zs, zs, (size_t)nsamp * sizeof(int), cudaMemcpyHostToDevice));
    if (!g_seeds)  NG_CK(cudaMalloc(&g_seeds,  (size_t)NG_CAP * sizeof(uint64_t)));
    if (!g_omin)   NG_CK(cudaMalloc(&g_omin,   (size_t)NG_CAP * sizeof(int)));
    if (!g_omax)   NG_CK(cudaMalloc(&g_omax,   (size_t)NG_CAP * sizeof(int)));
    if (!g_ocount) NG_CK(cudaMalloc(&g_ocount, (size_t)NG_CAP * sizeof(int)));

    {
        // octave count per climate param (matches Ng_derClimate) -> compact pool stride.
        static const int CLIM_K[6] = { 4, 4, 18, 8, 32, 6 };
        static const int CLIM_NZ[6] = { 2, 2, 9, 4, 3, 3 };   // non-zero amplitudes per perlin
        int  K     = (para >= 0 && para < 6) ? CLIM_K[para] : 0;
        if (K > 0 && nmax > 0) {   // "up to octave": na + nb octaves (xDoublePerlinInit split)
            int na = (nmax + 1) >> 1, nb = nmax - na, nz = CLIM_NZ[para];
            K = (na < nz ? na : nz) + (nb < nz ? nb : nz);
            if (K < 1) K = 1;
        }
        size_t per_seed = (size_t)K * sizeof(PerlinG) + sizeof(DPerlinG)
                        + (size_t)K * sizeof(OctSeedG) + 3 * sizeof(int);
        // seeds per block: fill the block under the DEVICE shared budget (cap 32)
        int  S = (K > 0) ? (int)((size_t)g_maxshm / per_seed) : 0;
        if (S > 32) S = 32;
        // multi-seed kernel: validated bit-exact vs the single-seed kernel on the
        // real GPU (noise_gpu_climate_diag: 0 mismatch / 8192 seeds, 3 configs).
        // The earlier recall loss was a units bug in the Python prefilter, not here.
        int  use_multi = (K > 0 && S >= 2);
        size_t shmem_multi = use_multi ? (size_t)S * per_seed : 0;
        if (shmem_multi > (size_t)g_maxshm) { use_multi = 0; shmem_multi = 0; }  // never exceed device limit
        size_t shmem_coop  = sizeof(BiomeNoiseG) + NG_OCT_TOTAL * sizeof(OctSeedG);
        // Lancements bornés en durée (~SSA_TARGET_MS) : l'unité du découpage est
        // « graine × point d'échantillonnage », donc indépendante de la taille de zone.
        static SsaChunker ck(1LL << 22, 1LL << 12, 1LL << 36);
        for (int off = 0; off < n; ) {
            int64_t _c = ck.chunk / (nsamp > 0 ? nsamp : 1);
            int _mins = ng_min_seeds(use_multi ? S : 1);
            if (_c < _mins) _c = _mins;
            if (_c > NG_CAP) _c = NG_CAP;
            int cur = (n - off < _c) ? (n - off) : (int)_c;
            ck.begin();
            NG_CK(cudaMemcpy(g_seeds, seeds + off, (size_t)cur * sizeof(uint64_t), cudaMemcpyHostToDevice));
            if (use_multi) {
                int nblocks = (cur + S - 1) / S;
                k_climatescan_multi<<<nblocks, NG_BLOCK, shmem_multi>>>(mc, para, nmax, K, S, lo10k, hi10k,
                    g_xs, g_zs, nsamp, g_seeds, cur, g_omin, g_omax, g_ocount);
            } else {
                k_climatescan_coop<<<cur, NG_BLOCK, shmem_coop>>>(mc, para, nmax, lo10k, hi10k, g_xs, g_zs,
                    nsamp, g_seeds, cur, g_omin, g_omax, g_ocount);
            }
            NG_CK(cudaGetLastError());
            NG_CK(cudaDeviceSynchronize());
            NG_CK(cudaMemcpy(out_min   + off, g_omin,   (size_t)cur * sizeof(int), cudaMemcpyDeviceToHost));
            NG_CK(cudaMemcpy(out_max   + off, g_omax,   (size_t)cur * sizeof(int), cudaMemcpyDeviceToHost));
            NG_CK(cudaMemcpy(out_count + off, g_ocount, (size_t)cur * sizeof(int), cudaMemcpyDeviceToHost));
            ck.end();
            off += cur;
        }
    }

done:
    return rc;
    #undef NG_CK
}

EXPORT int noise_gpu_climatescan(
    int mc, int para, int lo10k, int hi10k,
    const int* xs, const int* zs, int nsamp,
    const uint64_t* seeds, int n,
    int* out_min, int* out_max, int* out_count)
{
    return climatescan_impl(mc, para, -1, lo10k, hi10k, xs, zs, nsamp, seeds, n,
                            out_min, out_max, out_count);
}

// Same with the parameter's octave limit ("up to octave", cubiomes' nmax; -1 = all).
EXPORT int noise_gpu_climatescan2(
    int mc, int para, int nmax, int lo10k, int hi10k,
    const int* xs, const int* zs, int nsamp,
    const uint64_t* seeds, int n,
    int* out_min, int* out_max, int* out_count)
{
    return climatescan_impl(mc, para, nmax, lo10k, hi10k, xs, zs, nsamp, seeds, n,
                            out_min, out_max, out_count);
}

// ---------------------------------------------------------------------------
// DIAGNOSTIC: run BOTH the single-seed and multi-seed climate kernels on the
// same seeds and report where they disagree (+ the position within the block,
// idx%S, which reveals a thread/block bug). Returns the mismatch count.
// ---------------------------------------------------------------------------

// Fused climate filter: seeds seed0 .. seed0+n-1 (uint64 wrap), survivors in
// out_seeds (at most cap, any order), their total count in *out_total (may be
// > cap: then the caller asks again with a smaller n). Returns 0, -1 (CUDA
// error), -4 (this parameter needs the per-seed fallback: use climatescan).
EXPORT int noise_gpu_climatefilter(
    int mc, int para, int nmax,
    const int* xs, const int* zs, int nsamp,
    uint64_t seed0, long long n, int mode, long long A, long long B,
    uint64_t* out_seeds, int cap, long long* out_total)
{
    std::lock_guard<std::mutex> _ng_guard(g_ng_lock);
    cudaError_t e; int rc = 0;
    #define NG_CK(call) do{ e=(call); if(e!=cudaSuccess){ \
        ssa_set_error(e, #call, __LINE__); rc=-1; goto done; } }while(0)
    static int fset = 0;
    static int f_maxshm = 48 * 1024;
    static uint64_t* f_out = nullptr;
    static int f_cap = 0;
    static unsigned int* f_n = nullptr;
    *out_total = 0;
    if (n <= 0) return 0;
    if (!fset) {
        NG_CK(cudaDeviceSetLimit(cudaLimitStackSize, 8*1024));
        int ms = 0;
        if (cudaDeviceGetAttribute(&ms, cudaDevAttrMaxSharedMemoryPerBlockOptin, 0) == cudaSuccess && ms > 0)
            f_maxshm = ms;
        else if (cudaDeviceGetAttribute(&ms, cudaDevAttrMaxSharedMemoryPerBlock, 0) == cudaSuccess && ms > 0)
            f_maxshm = ms;
        cudaFuncSetAttribute(k_climatefilter_multi, cudaFuncAttributeMaxDynamicSharedMemorySize, f_maxshm);
        cudaFuncSetAttribute(k_climatefilter_multi, cudaFuncAttributePreferredSharedMemoryCarveout,
                             cudaSharedmemCarveoutMaxShared);
        fset = 1;
    }
    if (nsamp > g_nsamp_cap) {
        cudaFree(g_xs); cudaFree(g_zs); g_xs = nullptr; g_zs = nullptr;
        NG_CK(cudaMalloc(&g_xs, (size_t)nsamp * sizeof(int)));
        NG_CK(cudaMalloc(&g_zs, (size_t)nsamp * sizeof(int)));
        g_nsamp_cap = nsamp;
    }
    NG_CK(cudaMemcpy(g_xs, xs, (size_t)nsamp * sizeof(int), cudaMemcpyHostToDevice));
    NG_CK(cudaMemcpy(g_zs, zs, (size_t)nsamp * sizeof(int), cudaMemcpyHostToDevice));
    if (cap > f_cap) {
        cudaFree(f_out); f_out = nullptr;
        NG_CK(cudaMalloc(&f_out, (size_t)cap * sizeof(uint64_t)));
        f_cap = cap;
    }
    if (!f_n) NG_CK(cudaMalloc(&f_n, sizeof(unsigned int)));
    NG_CK(cudaMemset(f_n, 0, sizeof(unsigned int)));
    {
        static const int CLIM_K[6] = { 4, 4, 18, 8, 32, 6 };
        static const int CLIM_NZ[6] = { 2, 2, 9, 4, 3, 3 };
        int K = (para >= 0 && para < 6) ? CLIM_K[para] : 0;
        if (K > 0 && nmax > 0) {
            int na = (nmax + 1) >> 1, nb = nmax - na, nz = CLIM_NZ[para];
            K = (na < nz ? na : nz) + (nb < nz ? nb : nz);
            if (K < 1) K = 1;
        }
        size_t per_seed = (size_t)K * sizeof(PerlinG) + sizeof(DPerlinG)
                        + (size_t)K * sizeof(OctSeedG) + 2 * sizeof(int);
        int S = (K > 0) ? (int)((size_t)f_maxshm / per_seed) : 0;
        if (S > 32) S = 32;
        if (K <= 0 || S < 2) { rc = -4; goto done; }
        size_t shmem = (size_t)S * per_seed;
        static SsaChunker ck(1LL << 24, 1LL << 12, 1LL << 40);
        for (long long off = 0; off < n; ) {
            long long _c = ck.chunk / (nsamp > 0 ? nsamp : 1);
            long long _mins = ng_min_seeds(S);
            if (_c < _mins) _c = _mins;
            if (_c > (1LL << 24)) _c = 1LL << 24;
            long long cur = (n - off < _c) ? (n - off) : _c;
            ck.begin();
            int nblocks = (int)((cur + S - 1) / S);
            k_climatefilter_multi<<<nblocks, NG_BLOCK, shmem>>>(mc, para, nmax, K, S,
                g_xs, g_zs, nsamp, seed0 + (uint64_t)off, (int)cur, mode, A, B, f_out, cap, f_n);
            NG_CK(cudaGetLastError());
            NG_CK(cudaDeviceSynchronize());
            ck.end();
            off += cur;
        }
        unsigned int hn = 0;
        NG_CK(cudaMemcpy(&hn, f_n, sizeof(unsigned int), cudaMemcpyDeviceToHost));
        *out_total = (long long)hn;
        int ncopy = (int)(hn < (unsigned int)cap ? hn : (unsigned int)cap);
        if (ncopy > 0)
            NG_CK(cudaMemcpy(out_seeds, f_out, (size_t)ncopy * sizeof(uint64_t), cudaMemcpyDeviceToHost));
    }
done:
    return rc;
    #undef NG_CK
}

EXPORT int noise_gpu_climate_diag(int mc, int para,
    const int* xs, const int* zs, int nsamp,
    const uint64_t* seeds, int n)
{
    std::lock_guard<std::mutex> _ng_guard(g_ng_lock);
    static const int CLIM_K[6] = { 4, 4, 18, 8, 32, 6 };
    int K = (para >= 0 && para < 6) ? CLIM_K[para] : 0;
    if (K <= 0) { fprintf(stderr, "[diag] bad para %d\n", para); return -1; }
    if (n > (1 << 16)) n = (1 << 16);
    int maxshm = 48*1024, ms = 0;   // device shared-mem budget (Pascal=48K, Volta+ more)
    if (cudaDeviceGetAttribute(&ms, cudaDevAttrMaxSharedMemoryPerBlockOptin, 0) == cudaSuccess && ms > 0)
        maxshm = ms;
    else if (cudaDeviceGetAttribute(&ms, cudaDevAttrMaxSharedMemoryPerBlock, 0) == cudaSuccess && ms > 0)
        maxshm = ms;
    size_t per_seed = (size_t)K*sizeof(PerlinG) + sizeof(DPerlinG)
                    + (size_t)K*sizeof(OctSeedG) + 3*sizeof(int);
    int S = (int)((size_t)maxshm / per_seed); if (S > 32) S = 32; if (S < 1) S = 1;
    size_t shmem_multi = (size_t)S * per_seed;
    size_t shmem_coop  = sizeof(BiomeNoiseG) + NG_OCT_TOTAL * sizeof(OctSeedG);
    cudaFuncSetAttribute(k_climatescan_multi, cudaFuncAttributeMaxDynamicSharedMemorySize, maxshm);
    cudaFuncSetAttribute(k_climatescan_multi, cudaFuncAttributePreferredSharedMemoryCarveout,
                         cudaSharedmemCarveoutMaxShared);

    int *dxs=0,*dzs=0,*sm=0,*sx=0,*sc=0,*mm=0,*mx=0,*mc2=0; uint64_t* ds=0;
    cudaMalloc(&dxs, (size_t)nsamp*sizeof(int)); cudaMalloc(&dzs, (size_t)nsamp*sizeof(int));
    cudaMalloc(&ds,  (size_t)n*sizeof(uint64_t));
    cudaMalloc(&sm,(size_t)n*4); cudaMalloc(&sx,(size_t)n*4); cudaMalloc(&sc,(size_t)n*4);
    cudaMalloc(&mm,(size_t)n*4); cudaMalloc(&mx,(size_t)n*4); cudaMalloc(&mc2,(size_t)n*4);
    cudaMemcpy(dxs, xs, (size_t)nsamp*sizeof(int), cudaMemcpyHostToDevice);
    cudaMemcpy(dzs, zs, (size_t)nsamp*sizeof(int), cudaMemcpyHostToDevice);
    cudaMemcpy(ds, seeds, (size_t)n*sizeof(uint64_t), cudaMemcpyHostToDevice);

    k_climatescan_coop<<<n, NG_BLOCK, shmem_coop>>>(mc, para, -1, -2147483647, 2147483647,
        dxs, dzs, nsamp, ds, n, sm, sx, sc);
    cudaDeviceSynchronize();
    int nblocks = (n + S - 1) / S;
    k_climatescan_multi<<<nblocks, NG_BLOCK, shmem_multi>>>(mc, para, -1, K, S, -2147483647, 2147483647,
        dxs, dzs, nsamp, ds, n, mm, mx, mc2);
    cudaError_t e = cudaGetLastError();
    if (e != cudaSuccess) fprintf(stderr, "[diag] multi launch error: %s\n", cudaGetErrorString(e));
    cudaDeviceSynchronize();

    int *hsm=(int*)malloc((size_t)n*4),*hsx=(int*)malloc((size_t)n*4);
    int *hmm=(int*)malloc((size_t)n*4),*hmx=(int*)malloc((size_t)n*4);
    cudaMemcpy(hsm, sm, (size_t)n*4, cudaMemcpyDeviceToHost);
    cudaMemcpy(hsx, sx, (size_t)n*4, cudaMemcpyDeviceToHost);
    cudaMemcpy(hmm, mm, (size_t)n*4, cudaMemcpyDeviceToHost);
    cudaMemcpy(hmx, mx, (size_t)n*4, cudaMemcpyDeviceToHost);

    int mism = 0, shown = 0;
    fprintf(stderr, "[diag] para=%d K=%d S=%d nsamp=%d n=%d (shmem_multi=%zu)\n",
            para, K, S, nsamp, n, shmem_multi);
    for (int i = 0; i < n; i++) {
        if (hsm[i] != hmm[i] || hsx[i] != hmx[i]) {
            if (shown < 24) {
                fprintf(stderr, "[diag] #%d blk=%d pos=%d | single[%d,%d] multi[%d,%d]\n",
                        i, i / S, i % S, hsm[i], hsx[i], hmm[i], hmx[i]);
                shown++;
            }
            mism++;
        }
    }
    fprintf(stderr, "[diag] MISMATCHES: %d / %d\n", mism, n);
    free(hsm); free(hsx); free(hmm); free(hmx);
    cudaFree(dxs); cudaFree(dzs); cudaFree(ds);
    cudaFree(sm); cudaFree(sx); cudaFree(sc); cudaFree(mm); cudaFree(mx); cudaFree(mc2);
    return mism;
}
