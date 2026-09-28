// ssa_cuda_common.cuh — socle commun à toutes les DLL CUDA de SSA.
//
//  * Contrôle d'erreur systématique : SSA_CK(expr) mémorise l'erreur CUDA
//    (nom, code, ligne, appel) puis fait `return -1` depuis la fonction appelante.
//    Les buffers device sont libérés automatiquement (SsaDevBuf, RAII) — plus de
//    fuite ni de `goto` au-dessus d'une initialisation.
//  * ssa_cuda_last_error()      → texte de la dernière erreur (pour l'UI / les logs)
//  * ssa_cuda_selftest()        → lance un VRAI kernel et vérifie son résultat.
//    C'est lui qui dit « le GPU est utilisable avec CETTE DLL » : une carte pour
//    laquelle la DLL ne contient pas de code échoue ici (no kernel image), au lieu
//    de renvoyer 0 résultat en silence plus tard.
//  * ssa_cuda_device_info()     → « nom | cc | VRAM | driver » du device 0.
//  * SsaChunker                 → découpe un gros lot en lancements d'environ
//    SSA_TARGET_MS pour ne jamais approcher le watchdog Windows (TDR, 2 s), quelle
//    que soit la vitesse de la carte, sans coût mesurable sur une carte rapide.
//
// Chaque DLL inclut ce fichier UNE fois (symboles statiques = une copie par DLL).
#pragma once
#include <cuda_runtime.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <chrono>

#ifdef _WIN32
  #define SSA_EXPORT extern "C" __declspec(dllexport)
#else
  #define SSA_EXPORT extern "C" __attribute__((visibility("default")))
#endif

#ifndef SSA_UNIT
  #define SSA_UNIT "cuda"
#endif

#ifndef SSA_TARGET_MS
  #define SSA_TARGET_MS 120.0   // durée visée par lancement (TDR Windows = 2000 ms)
#endif

static char g_ssa_err[640]  = "";
static int  g_ssa_err_code  = 0;

static void ssa_set_error(cudaError_t e, const char *what, int line) {
    g_ssa_err_code = (int)e;
    snprintf(g_ssa_err, sizeof g_ssa_err, "%s (%d): %s  [%s:%d, %s]",
             cudaGetErrorName(e), (int)e, cudaGetErrorString(e), SSA_UNIT, line, what);
    fprintf(stderr, "[%s] CUDA ERROR %s\n", SSA_UNIT, g_ssa_err);
    (void)cudaGetLastError();   // efface l'erreur si elle n'est pas « collante »
}

static void ssa_set_msg(int code, const char *msg, int line) {
    g_ssa_err_code = code;
    snprintf(g_ssa_err, sizeof g_ssa_err, "%s  [%s:%d]", msg, SSA_UNIT, line);
    fprintf(stderr, "[%s] ERROR %s\n", SSA_UNIT, g_ssa_err);
}

// Retourne `ret` depuis la fonction appelante si l'appel CUDA échoue.
#define SSA_CK_RET(expr, ret) do { cudaError_t _e = (expr); \
    if (_e != cudaSuccess) { ssa_set_error(_e, #expr, __LINE__); return (ret); } } while (0)
#define SSA_CK(expr) SSA_CK_RET(expr, -1)
// Après un lancement <<<>>> : erreur de lancement puis erreur d'exécution.
#define SSA_CK_LAUNCH() do { SSA_CK(cudaGetLastError()); SSA_CK(cudaDeviceSynchronize()); } while (0)

// Buffer device libéré automatiquement à la sortie de la fonction.
struct SsaDevBuf {
    void *p = nullptr;
    SsaDevBuf() {}
    SsaDevBuf(const SsaDevBuf &) = delete;
    SsaDevBuf &operator=(const SsaDevBuf &) = delete;
    ~SsaDevBuf() { if (p) cudaFree(p); }
    cudaError_t alloc(size_t bytes) { return cudaMalloc(&p, bytes ? bytes : 1); }
    template <class T> T *as() const { return (T *)p; }
};

// Découpe adaptative en lancements d'environ SSA_TARGET_MS.
struct SsaChunker {
    int64_t chunk;             // unités par lancement (graines, lows, layouts…)
    int64_t min_chunk, max_chunk;
    SsaChunker(int64_t start, int64_t mn, int64_t mx) : chunk(start), min_chunk(mn), max_chunk(mx) {}
    std::chrono::high_resolution_clock::time_point t0;
    void begin() { t0 = std::chrono::high_resolution_clock::now(); }
    void end() {
        double ms = std::chrono::duration<double, std::milli>(
            std::chrono::high_resolution_clock::now() - t0).count();
        if (ms < SSA_TARGET_MS * 0.4 && chunk < max_chunk)      chunk *= 2;
        else if (ms > SSA_TARGET_MS * 1.6 && chunk > min_chunk) chunk /= 2;
        if (chunk < min_chunk) chunk = min_chunk;
        if (chunk > max_chunk) chunk = max_chunk;
    }
};

__global__ void k_ssa_selftest(uint32_t *out, uint32_t n) {
    uint32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) out[i] = (i * 2654435761u) ^ 0x5A5A5A5Au;
}

SSA_EXPORT const char *ssa_cuda_last_error(void) { return g_ssa_err; }
SSA_EXPORT int ssa_cuda_last_error_code(void) { return g_ssa_err_code; }

// 0 = OK. >0 = code d'erreur CUDA. -2 = aucun GPU. -3 = résultat faux.
SSA_EXPORT int ssa_cuda_selftest(void) {
    int n = 0;
    cudaError_t e = cudaGetDeviceCount(&n);
    if (e != cudaSuccess) { ssa_set_error(e, "cudaGetDeviceCount", __LINE__); return (int)e; }
    if (n <= 0) { ssa_set_msg(-2, "no NVIDIA GPU detected", __LINE__); return -2; }
    const uint32_t N = 1024;
    SsaDevBuf b;
    e = b.alloc(N * sizeof(uint32_t));
    if (e != cudaSuccess) { ssa_set_error(e, "cudaMalloc", __LINE__); return (int)e; }
    k_ssa_selftest<<<(N + 255) / 256, 256>>>(b.as<uint32_t>(), N);
    e = cudaGetLastError();
    if (e == cudaSuccess) e = cudaDeviceSynchronize();
    if (e != cudaSuccess) { ssa_set_error(e, "k_ssa_selftest", __LINE__); return (int)e; }
    uint32_t h[N];
    e = cudaMemcpy(h, b.p, sizeof h, cudaMemcpyDeviceToHost);
    if (e != cudaSuccess) { ssa_set_error(e, "cudaMemcpy", __LINE__); return (int)e; }
    for (uint32_t i = 0; i < N; i++)
        if (h[i] != ((i * 2654435761u) ^ 0x5A5A5A5Au)) {
            ssa_set_msg(-3, "GPU self-test: wrong result", __LINE__); return -3;
        }
    g_ssa_err[0] = 0; g_ssa_err_code = 0;
    return 0;
}

// Écrit « nom|cc|vram_Mo|driver » dans buf. 0 = OK.
SSA_EXPORT int ssa_cuda_device_info(char *buf, int cap) {
    if (!buf || cap <= 0) return -1;
    buf[0] = 0;
    cudaDeviceProp pr;
    cudaError_t e = cudaGetDeviceProperties(&pr, 0);
    if (e != cudaSuccess) { ssa_set_error(e, "cudaGetDeviceProperties", __LINE__); return (int)e; }
    int drv = 0; cudaDriverGetVersion(&drv);
    snprintf(buf, cap, "%s|%d.%d|%zu|%d.%d", pr.name, pr.major, pr.minor,
             (size_t)(pr.totalGlobalMem >> 20), drv / 1000, (drv % 1000) / 10);
    return 0;
}
