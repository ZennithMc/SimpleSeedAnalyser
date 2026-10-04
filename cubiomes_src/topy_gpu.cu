// topy_gpu.cu — Top Y of 1.13–1.17 strongholds on the GPU (03/10).
//
// One thread = one stronghold (48-bit family, start chunk): the generator of
// topy_gpu_gen.cuh, the same code that is checked against the CPU generator.
// The GPU only answers "which (family, chunk) reach the threshold"; the CPU then
// finds a seed of the family that lands on that chunk and re-checks it exactly.
//
//   topy_gpu_scan(f0, nfam, cx0[], cz0[], thr, pre118, sea, margin, minY, inflate,
//                 out_fam[], out_cx[], out_cz[], out_top[], cap)
//     families f0 … f0+nfam-1 (48 low bits); cx0/cz0 = first chunk of the
//     15 × 15 chunks each family's snap can reach. Writes the strongholds whose
//     Top Y >= thr (or TG_OVERFLOW = "check it on the CPU"). Returns their count
//     (it can exceed cap: then call again with fewer families), or -1 on error.
#define SSA_UNIT "topy_gpu"
#include "ssa_cuda_common.cuh"
#include "topy_gpu_gen.cuh"

#define TOPY_SIDE 15
#define TOPY_PER_FAM (TOPY_SIDE * TOPY_SIDE)

// Persistent threads: as many as the GPU holds at once, each one takes the next
// stronghold from an atomic counter until the launch's work is done. A stronghold
// takes a very variable time (50 to 500 pieces): this keeps every SM full until
// the end of a launch instead of waiting for the slowest thread of each block.
// MINB = blocks of 128 threads per SM the compiler must fit (registers): more
// threads hide more memory latency but may spill. The variants are measured by
// tools/topy_gpu_bench.py; topy_gpu_set_variant picks one.
template <int MINB>
__global__ void __launch_bounds__(128, MINB)
k_topy(uint64_t f0, uint32_t fam_off, uint32_t nfam, const int16_t *cx0, const int16_t *cz0,
       int thr, int pre118, int sea, int margin, int minY, int inflate, uint32_t *work,
       uint32_t *count, uint32_t cap, uint32_t *ofam, int16_t *ocx, int16_t *ocz, int16_t *otop) {
    TgGen g;
    tg_init(&g);
    const uint32_t total = nfam * TOPY_PER_FAM;
    for (;;) {
        uint32_t t = atomicAdd(work, 1u);
        if (t >= total) return;
        uint32_t fam = fam_off + t / TOPY_PER_FAM;
        int j = (int)(t % TOPY_PER_FAM);
        int cx = cx0[fam] + j / TOPY_SIDE, cz = cz0[fam] + j % TOPY_SIDE;
        int64_t seed = (int64_t)((f0 + fam) & ((1ULL << 48) - 1));
        int top = tg_topy(&g, seed, cx, cz, sea, margin, minY, inflate, pre118);
        if (top >= thr || top == TG_OVERFLOW) {
            uint32_t k = atomicAdd(count, 1u);
            if (k < cap) { ofam[k] = fam; ocx[k] = (int16_t)cx; ocz[k] = (int16_t)cz; otop[k] = (int16_t)top; }
        }
    }
}

#define TOPY_TARGET_MS 250.0          // per launch: well under the Windows watchdog (2 s)

typedef void (*TopyKernel)(uint64_t, uint32_t, uint32_t, const int16_t *, const int16_t *, int, int,
                           int, int, int, int, uint32_t *, uint32_t *, uint32_t, uint32_t *,
                           int16_t *, int16_t *, int16_t *);
static const TopyKernel K_VARIANTS[] = { k_topy<8>, k_topy<10>, k_topy<12>, k_topy<6> };
#define TOPY_NVAR 4
static int g_variant = 0;                    // default: 8 blocks / SM (best on an RTX 5080, 03/10)

static int g_resident = 0, g_blocks = 0;     // threads / blocks the GPU holds at once
static double g_last_ms = 0, g_chunk = 0;    // last launch (diagnostics)
static uint32_t g_fam_chunk = 0;             // families per launch (adaptive, kept between calls)

static cudaError_t topy_occupancy(void) {
    if (g_resident) return cudaSuccess;
    int dev = 0, sms = 0, per_sm = 0;
    TopyKernel k = K_VARIANTS[g_variant];
    cudaError_t e = cudaGetDevice(&dev);
    if (e == cudaSuccess) e = cudaDeviceGetAttribute(&sms, cudaDevAttrMultiProcessorCount, dev);
    // no shared memory: give the SM's whole on-chip memory to the L1 cache
    if (e == cudaSuccess) e = cudaFuncSetAttribute((const void *)k,
                                                    cudaFuncAttributePreferredSharedMemoryCarveout, 0);
    if (e == cudaSuccess) e = cudaOccupancyMaxActiveBlocksPerMultiprocessor(&per_sm, k, 128, 0);
    if (e != cudaSuccess) return e;
    if (per_sm < 1) per_sm = 1;
    g_blocks = per_sm * sms;
    g_resident = g_blocks * 128;
    g_fam_chunk = (uint32_t)((2 * (int64_t)g_resident) / TOPY_PER_FAM + 1);
    return cudaSuccess;
}

SSA_EXPORT int topy_gpu_scan(uint64_t f0, uint32_t nfam, const int16_t *cx0, const int16_t *cz0,
                             int thr, int pre118, int sea, int margin, int minY, int inflate,
                             uint32_t *out_fam, int16_t *out_cx, int16_t *out_cz, int16_t *out_top,
                             uint32_t cap) {
    if (nfam == 0) return 0;
    SSA_CK(topy_occupancy());
    SsaDevBuf dcx, dcz, dcount, dwork, dfam, docx, docz, dtop;
    SSA_CK(dcx.alloc(nfam * sizeof(int16_t)));
    SSA_CK(dcz.alloc(nfam * sizeof(int16_t)));
    SSA_CK(dcount.alloc(sizeof(uint32_t)));
    SSA_CK(dwork.alloc(sizeof(uint32_t)));
    SSA_CK(dfam.alloc((size_t)cap * sizeof(uint32_t)));
    SSA_CK(docx.alloc((size_t)cap * sizeof(int16_t)));
    SSA_CK(docz.alloc((size_t)cap * sizeof(int16_t)));
    SSA_CK(dtop.alloc((size_t)cap * sizeof(int16_t)));
    SSA_CK(cudaMemcpy(dcx.p, cx0, nfam * sizeof(int16_t), cudaMemcpyHostToDevice));
    SSA_CK(cudaMemcpy(dcz.p, cz0, nfam * sizeof(int16_t), cudaMemcpyHostToDevice));
    SSA_CK(cudaMemset(dcount.p, 0, sizeof(uint32_t)));
    const uint32_t min_fam = (uint32_t)(g_resident / TOPY_PER_FAM + 1);    // one full wave
    for (uint32_t off = 0; off < nfam;) {
        uint32_t n = nfam - off < g_fam_chunk ? nfam - off : g_fam_chunk;
        uint64_t items = (uint64_t)n * TOPY_PER_FAM;
        unsigned blocks = (unsigned)g_blocks;
        if ((uint64_t)blocks * 128 > items) blocks = (unsigned)((items + 127) / 128);
        SSA_CK(cudaMemset(dwork.p, 0, sizeof(uint32_t)));
        auto t0 = std::chrono::high_resolution_clock::now();
        K_VARIANTS[g_variant]<<<blocks, 128>>>(f0, off, n, dcx.as<int16_t>(), dcz.as<int16_t>(), thr, pre118, sea,
                                margin, minY, inflate, dwork.as<uint32_t>(), dcount.as<uint32_t>(), cap,
                                dfam.as<uint32_t>(), docx.as<int16_t>(), docz.as<int16_t>(),
                                dtop.as<int16_t>());
        SSA_CK_LAUNCH();
        double ms = std::chrono::duration<double, std::milli>(
            std::chrono::high_resolution_clock::now() - t0).count();
        g_last_ms = ms; g_chunk = n;
        // next launches: aim at TOPY_TARGET_MS (only full-size launches teach the rate)
        if (n == g_fam_chunk && ms > 0) {
            double want = (double)n * TOPY_TARGET_MS / ms;
            if (want > 2.0 * n) want = 2.0 * n;
            if (want < 0.5 * n) want = 0.5 * n;
            if (want < min_fam) want = min_fam;
            if (want > (1u << 20)) want = (1u << 20);
            g_fam_chunk = (uint32_t)want;
        }
        off += n;
    }
    uint32_t cnt = 0;
    SSA_CK(cudaMemcpy(&cnt, dcount.p, sizeof cnt, cudaMemcpyDeviceToHost));
    uint32_t m = cnt < cap ? cnt : cap;
    if (m) {
        SSA_CK(cudaMemcpy(out_fam, dfam.p, m * sizeof(uint32_t), cudaMemcpyDeviceToHost));
        SSA_CK(cudaMemcpy(out_cx, docx.p, m * sizeof(int16_t), cudaMemcpyDeviceToHost));
        SSA_CK(cudaMemcpy(out_cz, docz.p, m * sizeof(int16_t), cudaMemcpyDeviceToHost));
        SSA_CK(cudaMemcpy(out_top, dtop.p, m * sizeof(int16_t), cudaMemcpyDeviceToHost));
    }
    return (int)cnt;
}

// Diagnostics: resident threads, blocks, families per launch, last launch (families, ms).
SSA_EXPORT int topy_gpu_stats(int *resident, int *blocks, int *fam_chunk, double *last_fam,
                              double *last_ms) {
    cudaError_t e = topy_occupancy();
    if (e != cudaSuccess) { ssa_set_error(e, "topy_occupancy", __LINE__); return -1; }
    *resident = g_resident; *blocks = g_blocks; *fam_chunk = (int)g_fam_chunk;
    *last_fam = g_chunk; *last_ms = g_last_ms;
    return 0;
}

// Kernel variant 0..3 (= 8, 10, 12, 6 blocks / SM); returns the previous one, -1 if invalid.
SSA_EXPORT int topy_gpu_set_variant(int v) {
    if (v < 0 || v >= TOPY_NVAR) return -1;
    int old = g_variant;
    if (v != g_variant) { g_variant = v; g_resident = 0; g_blocks = 0; }
    return old;
}

// marker: the generator understands inflate | 256 = top of the portal room (03/10)
SSA_EXPORT int topy_gpu_room(void) { return 1; }

// Same generator on the CPU of this library (to compare inside one process).
SSA_EXPORT int topy_cpu_one(int64_t seed, int cx, int cz, int sea, int margin, int minY,
                            int inflate, int pre118) {
    static TgGen g;
    static int ready = 0;
    if (!ready) { tg_init(&g); ready = 1; }
    return tg_topy(&g, seed, cx, cz, sea, margin, minY, inflate, pre118);
}
