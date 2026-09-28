// biome_gpu.cu — CUDA kernel: 1 thread = 1 seed, computes the 128 stronghold
// positions (1.16) with a fully bit-exact biome snap. The device code lives in
// biome_gpu_kernel.cuh (validated bit-exact vs cubiomes). The host uploads the
// seed-INDEPENDENT layer template once; each thread derives its own per-seed
// layer seeds + ocean Perlin and runs the snap. Batch size adapts to the GPU's
// free VRAM (portable across cards).
//
// Self-test  : nvcc -O3 -arch=sm_120 -DBIOME_GPU_SELFTEST biome_gpu.cu -o biome_gpu_test
//              (validates GPU output vs embedded cubiomes reference + prints sps)
// DLL (SSA)  : nvcc -O3 -arch=sm_120 -shared biome_gpu.cu -o biome_gpu.dll
//
// Export:
//   biome_gpu_strongholds128(tmpl, oceanmix, validB, validM, seeds[], n,
//                            out_x[], out_z[]) -> 0 ok / -1 CUDA error
//   out_x/out_z are n*128 ints (row-major per seed).

#include <cuda_runtime.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "biome_gpu_kernel.cuh"   // GPUDEV=__device__, GPUMEM=__constant__, S_strongholds128

#ifdef _WIN32
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT __attribute__((visibility("default")))
#endif

// per-thread scratch = [LayerG gl[GL_MAX]][PerlinNoiseG pn][int gen[60000]], contiguous.
// (An interleaved/coalesced layout was tried and made it SLOWER — this kernel is bound by
//  per-thread cache locality of the gen buffer, not by inter-thread coalescing.)
#define BG_GEN_INTS 60000
#define BG_SCRATCH_BYTES ( (size_t)GL_MAX*sizeof(LayerG) + sizeof(PerlinNoiseG) + (size_t)BG_GEN_INTS*sizeof(int) )

// ── eye-count prefilter (1.13-1.17 LCG decoration RNG; reuses the J_* Java RNG from
//    biome_gpu_kernel.cuh). grid-max single-chunk count around the EXACT stronghold chunk =
//    the same prefilter SSA runs on CPU (seedlist_eyes_prefilter_arr) -> identical recall,
//    but now on GPU (compute hides in the memory-bound biome snap = ~free), so the CPU only
//    exact-validates the few survivors and stays cool.
__device__ inline int64_t bg_jlong(uint64_t* s){
    int32_t hi=J_next(s,32), lo=J_next(s,32);
    return (int64_t)(((uint64_t)(int64_t)hi<<32) + (uint64_t)(int64_t)lo);
}
__device__ inline uint64_t bg_deco_lcg(uint64_t ws, int32_t cx, int32_t cz){
    uint64_t s; J_setSeed(&s, ws);
    uint64_t a=(uint64_t)bg_jlong(&s)|1ULL, b=(uint64_t)bg_jlong(&s)|1ULL;
    uint64_t bx=(uint64_t)((int64_t)cx*16), bz=(uint64_t)((int64_t)cz*16);
    return (bx*a + bz*b) ^ ws;
}
__device__ inline int bg_eyes_chunk(uint64_t ws, int32_t cx, int32_t cz){
    uint64_t deco=bg_deco_lcg(ws,cx,cz);
    uint64_t s; J_setSeed(&s, deco+50000ULL);
    s=(s*0xe5cfdccc10e1ULL + 0x2fc9e05b45b8ULL)&((1ULL<<48)-1);  // O(1) skip of the PortalRoom prefix
    int eyes=0;
    #pragma unroll
    for(int i=0;i<12;i++){ uint32_t bits=(uint32_t)J_next(&s,24); if(bits>=15099495u) eyes++; }
    return eyes;
}
#ifndef BG_EYE_GRID
#define BG_EYE_GRID 7          // radius -> 15×15, matches the CPU prefilter (recall-safe)
#endif
__device__ inline int bg_eye_prefilter(uint64_t ws, int32_t cx, int32_t cz){
    int mx=0;
    for(int dz=-BG_EYE_GRID; dz<=BG_EYE_GRID; dz++)
        for(int dx=-BG_EYE_GRID; dx<=BG_EYE_GRID; dx++){
            int e=bg_eyes_chunk(ws, cx+dx, cz+dz);
            if(e>mx){ mx=e; if(mx>=12) return 12; }   // early-out at the max
        }
    return mx;
}

__global__ void k_strongholds128(int mc, const uint64_t* seeds, int n, uint64_t vB, uint64_t vM,
                                 char* scratch_base, size_t scratch_stride,
                                 int* out_x, int* out_z, int* out_eyes)
{
    int tid = blockIdx.x*blockDim.x + threadIdx.x;
    if (tid >= n) return;
    void* scratch = scratch_base + (size_t)tid * scratch_stride;
    int* ox = out_x + (size_t)tid*128;
    int* oz = out_z + (size_t)tid*128;
    S_strongholds128(mc, seeds[tid], vB, vM, scratch, ox, oz);   // 128 exact positions (memory-bound)
    uint64_t ws = (uint64_t)seeds[tid];
    int* oe = out_eyes + (size_t)tid*128;
    for (int i = 0; i < 128; i++)                                // fused eye prefilter (compute, ~free)
        oe[i] = bg_eye_prefilter(ws, ox[i] >> 4, oz[i] >> 4);
}

// mc: 1.13=16, 1.14=17, 1.15=18, 1.16=20, 1.17=21 (layer-stack versions only; NOT 1.18+).
extern "C" EXPORT int biome_gpu_strongholds128(
    int mc, const LayerG* tmpl, int oceanmix, uint64_t validB, uint64_t validM,
    const int64_t* seeds, int n, int* out_x, int* out_z, int* out_eyes)
{
    if (n <= 0) return 0;
    cudaError_t e;
    #define BG_CK(call) do{ e=(call); if(e!=cudaSuccess){ \
        fprintf(stderr,"[biome_gpu] %s -> %s\n", #call, cudaGetErrorString(e)); rc=-1; } }while(0)
    int rc = 0;
    // the 1.16 layer stack recurses ~30 deep -> raise the per-thread stack (default 1 KB).
    // Keep it as LOW as safely possible: stack lives in local memory and is reserved for
    // every resident thread, so a large value crushes occupancy. ~8 KB needed; 16 KB margin.
    BG_CK(cudaDeviceSetLimit(cudaLimitStackSize, 16*1024));
    BG_CK(cudaMemcpyToSymbol(GL_TMPL, tmpl, (size_t)GL_MAX*sizeof(LayerG)));
    BG_CK(cudaMemcpyToSymbol(GL_OCEANMIX, &oceanmix, sizeof(int)));
    if (rc) return -1;

    // ---- adaptive batch from free VRAM (portable to any card) ----
    size_t freeB = 0, totalB = 0;
    cudaMemGetInfo(&freeB, &totalB);
    size_t stride = (BG_SCRATCH_BYTES + 255) & ~(size_t)255;  // 256-byte aligned / thread
    const size_t per_thread = stride + 128*3*sizeof(int) + sizeof(uint64_t); // +x +z +eyes +seed
    size_t budget = (size_t)((double)freeB * 0.80);
    long long maxthreads = (long long)(budget / per_thread);
    if (maxthreads < 1) maxthreads = 1;
    int batch = (int)((maxthreads < (long long)n) ? maxthreads : (long long)n);
    if (batch < 1) batch = 1;

    uint64_t* d_seeds = NULL; int *d_x = NULL, *d_z = NULL, *d_eyes = NULL; char* d_scratch = NULL;
    BG_CK(cudaMalloc(&d_seeds,  (size_t)batch*sizeof(uint64_t)));
    if (!rc) BG_CK(cudaMalloc(&d_x, (size_t)batch*128*sizeof(int)));
    if (!rc) BG_CK(cudaMalloc(&d_z, (size_t)batch*128*sizeof(int)));
    if (!rc) BG_CK(cudaMalloc(&d_eyes, (size_t)batch*128*sizeof(int)));
    if (!rc) BG_CK(cudaMalloc(&d_scratch, (size_t)batch*stride));

    if (!rc) {
        #ifndef BG_BLOCK
        #define BG_BLOCK 32   // measured best on RTX 5080 (memory-bound; block size barely matters)
        #endif
        const int blockSize = BG_BLOCK;   // tune via nvcc -DBG_BLOCK=64/128/256
        for (int off = 0; off < n; off += batch) {
            int cur = (n - off < batch) ? (n - off) : batch;
            cudaMemcpy(d_seeds, seeds + off, (size_t)cur*sizeof(uint64_t), cudaMemcpyHostToDevice);
            int grid = (cur + blockSize - 1) / blockSize;
            k_strongholds128<<<grid, blockSize>>>(mc, d_seeds, cur, validB, validM,
                                                  d_scratch, stride, d_x, d_z, d_eyes);
            BG_CK(cudaDeviceSynchronize());
            if (rc) break;
            cudaMemcpy(out_x + (size_t)off*128, d_x, (size_t)cur*128*sizeof(int), cudaMemcpyDeviceToHost);
            cudaMemcpy(out_z + (size_t)off*128, d_z, (size_t)cur*128*sizeof(int), cudaMemcpyDeviceToHost);
            cudaMemcpy(out_eyes + (size_t)off*128, d_eyes, (size_t)cur*128*sizeof(int), cudaMemcpyDeviceToHost);
        }
    }
    #undef BG_CK
    if (d_seeds)   cudaFree(d_seeds);
    if (d_x)       cudaFree(d_x);
    if (d_z)       cudaFree(d_z);
    if (d_eyes)    cudaFree(d_eyes);
    if (d_scratch) cudaFree(d_scratch);
    return rc;
}

#ifdef BIOME_GPU_SELFTEST
#include "biome_gpu_testdata.h"
int main()
{
    cudaSetDevice(0);
    cudaDeviceProp prop; cudaGetDeviceProperties(&prop, 0);
    size_t freeB=0,totalB=0; cudaMemGetInfo(&freeB,&totalB);
    printf("GPU: %s  (sm_%d%d, %d SMs, %.1f GB free)\n", prop.name, prop.major, prop.minor,
           prop.multiProcessorCount, freeB/1073741824.0);

    // ---- 1) correctness: GPU vs embedded cubiomes reference ----
    int n = TD_NSEEDS;
    int64_t seeds[8]; for (int i=0;i<n;i++) seeds[i] = (int64_t)TD_seeds[i];
    int *ox = (int*)malloc((size_t)n*128*sizeof(int));
    int *oz = (int*)malloc((size_t)n*128*sizeof(int));
    int *oe = (int*)malloc((size_t)n*128*sizeof(int));
    int rc = biome_gpu_strongholds128(MC_1_16, TD_tmpl, TD_oceanmix, TD_vB, TD_vM, seeds, n, ox, oz, oe);
    if (rc) { printf("GPU call FAILED (CUDA error). Check arch/driver.\n"); return 1; }
    int tot=0, mism=0;
    for (int s=0;s<n;s++){
        int m=0;
        for (int i=0;i<128;i++){ tot++;
            if (ox[s*128+i]!=TD_refx[s][i] || oz[s*128+i]!=TD_refz[s][i]) { m++; mism++; } }
        printf("seed %-12llu : %d/128 %s\n", (unsigned long long)TD_seeds[s], 128-m,
               m? "*** MISMATCH ***":"OK");
    }
    printf("%s (%d/%d)\n", mism==0?
        "==== GPU 128-STRONGHOLDS BIT-EXACT vs cubiomes ====":"==== MISMATCH ====",
        tot-mism, tot);
    // eye prefilter sanity (fused on GPU): range + how many strongholds reach 12
    int maxe=0, n12=0; for (int i=0;i<n*128;i++){ if(oe[i]>maxe)maxe=oe[i]; if(oe[i]>=12)n12++; }
    printf("eye prefilter (fused): max=%d, #(>=12)=%d / %d strongholds\n", maxe, n12, n*128);
    free(ox); free(oz); free(oe);
    if (mism) return 1;

    // ---- 2) throughput: sequential seeds, timed ----
    int N = 200000;
    int64_t* bs = (int64_t*)malloc((size_t)N*sizeof(int64_t));
    for (int i=0;i<N;i++) bs[i] = 1000000LL + i;
    int* bx = (int*)malloc((size_t)N*128*sizeof(int));
    int* bz = (int*)malloc((size_t)N*128*sizeof(int));
    int* be = (int*)malloc((size_t)N*128*sizeof(int));
    cudaEvent_t t0,t1; cudaEventCreate(&t0); cudaEventCreate(&t1);
    cudaEventRecord(t0);
    biome_gpu_strongholds128(MC_1_16, TD_tmpl, TD_oceanmix, TD_vB, TD_vM, bs, N, bx, bz, be);
    cudaEventRecord(t1); cudaEventSynchronize(t1);
    float ms=0; cudaEventElapsedTime(&ms, t0, t1);
    printf("\nthroughput: %d seeds (128-stronghold snap + fused eye prefilter) in %.1f ms"
           "  =>  %.0f seeds/s\n", N, ms, N/(ms/1000.0));
    free(bs); free(bx); free(bz); free(be);
    return 0;
}
#endif
