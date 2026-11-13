// tree_cuda.cu — (05/11) the GPU side of SSA's tree cracker (filters 1-3).
//
//   tree_cuda_scan(chunk, row0, nrows, out[], cap)
//     Walks rows [row0, row0 + nrows) of the first tree's lattice (treecrack_core.h):
//     one thread per lattice point tests tree 0 (filter 1), then one thread per
//     survivor checks that every other tree starts within the window (filters 2-3).
//     Writes the candidates (the state just before tree 0) to out; returns their
//     count (it may exceed cap: only cap are written), or -1 on a CUDA error.
//     Filter 4 (exact replay), population reversal and the other chunks run on the
//     CPU (treecrack.cpp): few candidates are left by then.
//   The rows are cut into launches of about SSA_TARGET_MS (Windows watchdog).
//
//   (06/11) sc_cuda_range(set, lows, nlows, g0, n, out, cap)
//     Structures → structure seed (structcrack_core.h): seed index g of
//     [g0, g0 + n) is (g mod 2^28) << 20 | lows[g >> 28] — every high part of every
//     low the lifting kept. Returns the seeds placing every structure.
#define SSA_UNIT "tree_cuda"
#include "ssa_cuda_common.cuh"
#include "treecrack_core.h"
#include "structcrack_core.h"

__constant__ TcChunk g_tc_chunk;
__constant__ TcLattice g_tc_lat;

__global__ void k_tc_filter1(int64_t row0, int64_t npts, uint64_t *buf, uint32_t *count, uint32_t cap) {
    int64_t idx = (int64_t)blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= npts) return;
    int64_t gi = idx % g_tc_lat.ci, gj = row0 + idx / g_tc_lat.ci;
    uint64_t s0;
    if (!tc_filter1(tc_lattice_state(&g_tc_lat, gi, gj), &g_tc_chunk, &s0)) return;
    uint32_t k = atomicAdd(count, 1u);
    if (k < cap) buf[k] = s0;
}

__global__ void k_tc_window(const uint64_t *buf, uint32_t n, uint64_t *out, uint32_t *count, uint32_t cap) {
    uint32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    uint64_t s0 = buf[i];
    if (!tc_filter_window(s0, &g_tc_chunk)) return;
    uint32_t k = atomicAdd(count, 1u);
    if (k < cap) out[k] = s0;
}

#define TC_BUF1 (1u << 23)          // filter-1 survivors per launch (64 MB)
#define TC_BUF2 (1u << 20)          // window survivors per launch

SSA_EXPORT int64_t tree_cuda_scan(const TcChunk *c, int64_t row0, int64_t nrows, uint64_t *out, int64_t cap) {
    TcLattice L;
    tc_lattice(&L, c->t[0].x, c->t[0].z);
    if (row0 < 0) row0 = 0;
    if (row0 + nrows > L.cj) nrows = L.cj - row0;
    if (nrows <= 0) return 0;
    SSA_CK(cudaMemcpyToSymbol(g_tc_chunk, c, sizeof(TcChunk)));
    SSA_CK(cudaMemcpyToSymbol(g_tc_lat, &L, sizeof(TcLattice)));
    SsaDevBuf b1, b2, cnt;
    SSA_CK(b1.alloc(sizeof(uint64_t) * TC_BUF1));
    SSA_CK(b2.alloc(sizeof(uint64_t) * TC_BUF2));
    SSA_CK(cnt.alloc(sizeof(uint32_t) * 2));
    uint32_t *d_cnt = cnt.as<uint32_t>();
    int64_t found = 0;
    // rows per launch: start at ~2^30 points, adapt to the target launch time
    int64_t start = (1LL << 30) / L.ci;
    if (start < 1) start = 1;
    SsaChunker ch(start, 1, (1LL << 33) / L.ci + 1);
    int64_t r = row0, end = row0 + nrows;
    while (r < end) {
        int64_t rows = ch.chunk;
        if (rows > end - r) rows = end - r;
        int64_t npts = rows * L.ci;
        ch.begin();
        SSA_CK(cudaMemset(d_cnt, 0, sizeof(uint32_t) * 2));
        k_tc_filter1<<<(unsigned)((npts + 255) / 256), 256>>>(r, npts, b1.as<uint64_t>(), d_cnt, TC_BUF1);
        SSA_CK_LAUNCH();
        uint32_t n1 = 0;
        SSA_CK(cudaMemcpy(&n1, d_cnt, sizeof n1, cudaMemcpyDeviceToHost));
        if (n1 > TC_BUF1) {                 // too many survivors: redo with fewer rows
            if (rows == 1) { ssa_set_msg(-4, "tree 0 alone keeps too many seeds", __LINE__); return -1; }
            ch.chunk = rows / 2;
            continue;
        }
        if (n1) {
            k_tc_window<<<(n1 + 255) / 256, 256>>>(b1.as<uint64_t>(), n1, b2.as<uint64_t>(), d_cnt + 1, TC_BUF2);
            SSA_CK_LAUNCH();
            uint32_t n2 = 0;
            SSA_CK(cudaMemcpy(&n2, d_cnt + 1, sizeof n2, cudaMemcpyDeviceToHost));
            if (n2 > TC_BUF2) n2 = TC_BUF2;
            int64_t room = cap - found;
            int64_t take = n2 < room ? n2 : (room > 0 ? room : 0);
            if (take > 0)
                SSA_CK(cudaMemcpy(out + found, b2.p, sizeof(uint64_t) * take, cudaMemcpyDeviceToHost));
            found += n2;
        }
        ch.end();
        r += rows;
    }
    return found;
}

// rows of the lattice of tree 0 at (x, z) — the progress unit
SSA_EXPORT int64_t tree_cuda_rows(int x, int z) {
    TcLattice L;
    tc_lattice(&L, x, z);
    return L.cj;
}

// ── (06/11) structures ──────────────────────────────────────────────────────────
__constant__ ScSet g_sc_set;

__global__ void k_sc(const uint32_t *lows, uint64_t lo0, uint64_t g0, uint64_t n, uint64_t *out,
                     uint32_t *count, uint32_t cap) {
    uint64_t i = (uint64_t)blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    uint64_t g = g0 + i;
    uint64_t ss = ((g & ((1ULL << 28) - 1)) << SC_LOWBITS) | (uint64_t)lows[(g >> 28) - lo0];
    if (!sc_test(&g_sc_set, ss)) return;
    uint32_t k = atomicAdd(count, 1u);
    if (k < cap) out[k] = ss;
}

#define SC_OUTCAP (1u << 20)

SSA_EXPORT int64_t sc_cuda_range(const ScSet *S, const uint32_t *lows, int64_t nlows, uint64_t g0,
                                 uint64_t n, uint64_t *out, int64_t cap) {
    uint64_t total = (uint64_t)nlows << 28;
    if (g0 >= total) return 0;
    if (g0 + n > total) n = total - g0;
    if (n == 0) return 0;
    SSA_CK(cudaMemcpyToSymbol(g_sc_set, S, sizeof(ScSet)));
    uint64_t lo0 = g0 >> 28, lo1 = (g0 + n - 1) >> 28;
    SsaDevBuf dl, dout, cnt;
    SSA_CK(dl.alloc(sizeof(uint32_t) * (lo1 - lo0 + 1)));
    SSA_CK(cudaMemcpy(dl.p, lows + lo0, sizeof(uint32_t) * (lo1 - lo0 + 1), cudaMemcpyHostToDevice));
    SSA_CK(dout.alloc(sizeof(uint64_t) * SC_OUTCAP));
    SSA_CK(cnt.alloc(sizeof(uint32_t)));
    uint32_t *d_cnt = cnt.as<uint32_t>();
    int64_t found = 0;
    SsaChunker ch(1LL << 28, 1LL << 20, 1LL << 34);
    uint64_t g = g0, end = g0 + n;
    while (g < end) {
        uint64_t m = (uint64_t)ch.chunk;
        if (m > end - g) m = end - g;
        ch.begin();
        SSA_CK(cudaMemset(d_cnt, 0, sizeof(uint32_t)));
        k_sc<<<(unsigned)((m + 255) / 256), 256>>>(dl.as<uint32_t>(), lo0, g, m, dout.as<uint64_t>(),
                                                     d_cnt, SC_OUTCAP);
        SSA_CK_LAUNCH();
        uint32_t k = 0;
        SSA_CK(cudaMemcpy(&k, d_cnt, sizeof k, cudaMemcpyDeviceToHost));
        uint32_t kk = k < SC_OUTCAP ? k : SC_OUTCAP;
        int64_t room = cap - found;
        int64_t take = (int64_t)kk < room ? (int64_t)kk : (room > 0 ? room : 0);
        if (take > 0)
            SSA_CK(cudaMemcpy(out + found, dout.p, sizeof(uint64_t) * take, cudaMemcpyDeviceToHost));
        found += k;
        ch.end();
        g += m;
    }
    return found;
}
