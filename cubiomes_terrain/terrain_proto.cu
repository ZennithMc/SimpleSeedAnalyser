/* terrain_proto.cu — FEASIBILITY benchmark for a GPU Exact-Y search.
 *
 * Question: can a GPU thread RE-SEED the terrain noise (build the ~40-64 Perlin
 * octave permutation tables for one world seed) fast enough that a batch of seeds
 * beats the CPU's ~90k seeds/s single-point rate? The surface SAMPLING already runs
 * on the GPU (terrain_gpu.cu, used by the map); only this per-seed re-seed is new.
 *
 * Each thread does the EXACT cubiomes per-octave init (xPerlinInit: 3 xNextDouble +
 * a 256-element Fisher-Yates shuffle via xNextInt), one octave at a time so only ONE
 * 257-byte permutation lives in local memory (~0.3 KB/thread → high occupancy).
 *
 * Build & run (NVIDIA, any 1.18+-capable card):
 *     nvcc -O3 terrain_proto.cu -o proto && ./proto
 * It prints the re-seed throughput in millions of seeds/s for a few octave counts.
 * Compare to the CPU's ~0.09 Mseeds/s. If it's >> that, the full GPU port is worth it.
 */
#include <cstdint>
#include <cstdio>
#include <cuda_runtime.h>

__device__ __forceinline__ uint64_t rotl64(uint64_t x, int b) {
    return (x << b) | (x >> (64 - b));
}
struct Xo { uint64_t lo, hi; };
__device__ __forceinline__ void xSetSeed(Xo *xr, uint64_t value) {
    const uint64_t XL=0x9e3779b97f4a7c15ULL, XH=0x6a09e667f3bcc909ULL;
    const uint64_t A=0xbf58476d1ce4e5b9ULL,  B=0x94d049bb133111ebULL;
    uint64_t l = value ^ XH, h = l + XL;
    l = (l ^ (l >> 30)) * A; h = (h ^ (h >> 30)) * A;
    l = (l ^ (l >> 27)) * B; h = (h ^ (h >> 27)) * B;
    xr->lo = l ^ (l >> 31); xr->hi = h ^ (h >> 31);
}
__device__ __forceinline__ uint64_t xNextLong(Xo *xr) {
    uint64_t l = xr->lo, h = xr->hi;
    uint64_t n = rotl64(l + h, 17) + l;
    h ^= l;
    xr->lo = rotl64(l, 49) ^ h ^ (h << 21);
    xr->hi = rotl64(h, 28);
    return n;
}
__device__ __forceinline__ int xNextInt(Xo *xr, uint32_t n) {
    uint64_t r = (xNextLong(xr) & 0xFFFFFFFF) * n;
    if ((uint32_t)r < n) {
        uint32_t lim = (~n + 1) % n;            /* 2^32 % n, via -n mod n */
        while ((uint32_t)r < lim) r = (xNextLong(xr) & 0xFFFFFFFF) * n;
    }
    return (int)(r >> 32);
}
__device__ __forceinline__ double xNextDouble(Xo *xr) {
    return (xNextLong(xr) >> (64 - 53)) * 1.1102230246251565E-16;
}

/* Per-thread: re-seed N_OCT octaves for one world seed, one permutation at a time.
 * Exact cost of cubiomes xPerlinInit per octave. A token sample keeps the compiler
 * from optimising the work away. */
__global__ void k_reseed(uint64_t seed0, int n, int n_oct, unsigned char *out) {
    int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    Xo base; xSetSeed(&base, seed0 + (uint64_t)i);
    uint64_t xlo = xNextLong(&base), xhi = xNextLong(&base);   /* like initBlendedNoise */
    unsigned acc = 0;
    unsigned char perm[257];                                   /* ONE octave at a time */
    for (int o = 0; o < n_oct; o++) {
        Xo oxr; oxr.lo = xlo ^ (0x9e3779b97f4a7c15ULL * (o + 1));
                oxr.hi = xhi ^ (0xc2b2ae3d27d4eb4fULL * (o + 1));
        double a = xNextDouble(&oxr) * 256.0;
        double b = xNextDouble(&oxr) * 256.0;
        double c = xNextDouble(&oxr) * 256.0;
        for (int k = 0; k < 256; k++) perm[k] = (unsigned char)k;
        for (int k = 0; k < 256; k++) {                        /* Fisher-Yates shuffle */
            int j = xNextInt(&oxr, 256 - k) + k;
            unsigned char t = perm[k]; perm[k] = perm[j]; perm[j] = t;
        }
        perm[256] = perm[0];
        /* token "sample": index the table a few times like a real noise lookup */
        int di = (o * 73) & 255;
        acc += perm[(int)a & 255] ^ perm[(int)b & 255] ^ perm[(int)c & 255] ^ perm[perm[di]];
    }
    out[i] = (unsigned char)acc;
}

static void bench(int n_oct, int n) {
    unsigned char *d_out; cudaMalloc(&d_out, n);
    int blk = 256, grid = (n + blk - 1) / blk;
    /* warm-up */
    k_reseed<<<grid, blk>>>(1ULL, n, n_oct, d_out); cudaDeviceSynchronize();
    cudaEvent_t a, b; cudaEventCreate(&a); cudaEventCreate(&b);
    cudaEventRecord(a);
    k_reseed<<<grid, blk>>>(1234567ULL, n, n_oct, d_out);
    cudaEventRecord(b); cudaEventSynchronize(b);
    float ms = 0; cudaEventElapsedTime(&ms, a, b);
    double mseeds = (double)n / (ms / 1000.0) / 1e6;
    printf("  octaves=%2d : %.1f Mseeds/s  (%.2f ms for %d seeds)\n", n_oct, mseeds, ms, n);
    cudaEventDestroy(a); cudaEventDestroy(b); cudaFree(d_out);
}

int main() {
    int dev = 0; cudaDeviceProp p; cudaGetDeviceProperties(&p, dev);
    printf("GPU: %s  (%d SMs)\n", p.name, p.multiProcessorCount);
    cudaError_t e = cudaGetLastError();
    if (e != cudaSuccess) { printf("cuda init err: %s\n", cudaGetErrorString(e)); return 1; }
    const int N = 16 * 1024 * 1024;     /* 16 M seeds */
    printf("Re-seed throughput (CPU single-point is ~0.09 Mseeds/s for comparison):\n");
    bench(40, N);   /* blended noise only */
    bench(64, N);   /* + climate */
    bench(80, N);   /* + caves-ish */
    e = cudaGetLastError();
    if (e != cudaSuccess) printf("kernel err: %s\n", cudaGetErrorString(e));
    return 0;
}
