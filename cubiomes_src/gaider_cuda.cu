// gaider_cuda.cu — CUDA kernel for Gaider 12-eye seed scanning.
//
// Compile (Linux):
//   nvcc -O3 -shared -Xcompiler "-fPIC" gaider_cuda.cu -o libgaider_cuda.so
// Compile (Windows):
//   nvcc -O3 -shared gaider_cuda.cu -o gaider_cuda.dll
//
// Provides one C-callable export:
//   gaider_cuda_scan_12eye(seed_start, n, mc_version, init_pos_x[], init_pos_z[],
//                           out_seeds[], out_eyes[], max_results) -> int matches
//
// `init_pos_x`/`init_pos_z` are pre-computed on CPU (one initFirstStronghold
// call per seed) and uploaded to the GPU. The GPU then runs the bit-perfect
// Gaider matrix check in parallel for each (seed, chunk_x, chunk_z).
//
// On RTX 5080 (~10000 SMs), expect ~5+ billion seeds/s for the GPU phase
// (excluding the CPU-side initFirstStronghold which can be parallelized too).

#include <cuda_runtime.h>
#include <stdint.h>
#define SSA_UNIT "gaider_cuda"
#include "ssa_cuda_common.cuh"

#ifdef _WIN32
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT __attribute__((visibility("default")))
#endif

#define XR_SILVER 0x6A09E667F3BCC909ULL
#define XR_GOLDEN 0x9E3779B97F4A7C15ULL
#define XR_MIX1   0xBF58476D1CE4E5B9ULL
#define XR_MIX2   0x94D049BB133111EBULL

// Auto-generated XOR matrices (must match CPU version)
__constant__ uint64_t XOR_LO[64][2] = {
    { 0xbea1cb0e235828b3ULL, 0x9729b1f34956cf87ULL },
    { 0x30d9b3a637b8cc30ULL, 0x69438152e6e4137bULL },
    { 0xef9ba2a91df9826aULL, 0x54d7aa11544d7f0dULL },
    { 0x6b77a2a23f77b567ULL, 0x184dd2690f183868ULL },
    { 0x8ee1a0478680083aULL, 0x66cb946dcd298822ULL },
    { 0x7396c0e3bb0fca9aULL, 0x715a6f94782b3af2ULL },
    { 0x9386458f6709c14aULL, 0xa57b30c71d13dd35ULL },
    { 0x77b6ca6dbf1381b0ULL, 0x860757166f353f18ULL },
    { 0xe98f1e3a0e68ee6bULL, 0x2eb84f434773c0f0ULL },
    { 0x48a8feb481412058ULL, 0x6ec4a811d3f5e13aULL },
    { 0x2acb074fd1528435ULL, 0x310cb9f1bb763692ULL },
    { 0x07cfecc893741e1eULL, 0x54445aa20b269e02ULL },
    { 0x175f9549f77a39adULL, 0xe9066c93af9dfdb0ULL },
    { 0x4426c48954682e38ULL, 0x62c2b5e8e0d69c9bULL },
    { 0x06eeb208c90ce28cULL, 0x5e5ef7e00ed35ea9ULL },
    { 0xb20ad12841018041ULL, 0xb1b3fcfb522ae01bULL },
    { 0xf94466f60cc9cfd9ULL, 0xfc7cf254b6da0494ULL },
    { 0x7420dd36650b4e03ULL, 0xe6ee807dd4f693c4ULL },
    { 0xa4d5e84842496c56ULL, 0x7ae8e208f7190436ULL },
    { 0x0a1651d2513089caULL, 0xd1ea97de107b32c9ULL },
    { 0xfa7bfb681850a9a3ULL, 0x5f5291a6283d5e3bULL },
    { 0xef063377f32c1b27ULL, 0xe075e04ebb0cd1a4ULL },
    { 0x8ee415201f9ac0d7ULL, 0x4a94290c864c26abULL },
    { 0x59d29bb6ecd74a35ULL, 0x5e5ddf9a3ab76eccULL },
    { 0x70d37d8062ede900ULL, 0x80b8f5bd4f4296e2ULL },
    { 0xc325d770d103a272ULL, 0x14babf1d5d7b1bf1ULL },
    { 0x1386ef197be907e6ULL, 0xfe7068c56f7362b3ULL },
    { 0x378500ecc90a9cf1ULL, 0xd22c04459a7949c0ULL },
    { 0xfe49609deaa81ae4ULL, 0x45f33f678cfa46dfULL },
    { 0xc8df216f835c4cdcULL, 0x0cbf3a8b7a964ff5ULL },
    { 0xadf96f98af5a1ec9ULL, 0xb0c0a793d68846d9ULL },
    { 0x814f25287c709c9bULL, 0x148a61faa04a5b72ULL },
    { 0xe0165b6fcb8e09f0ULL, 0xa500b82a5dd0442fULL },
    { 0xa559a881ba91e661ULL, 0x4ab3bd9ea43a43fcULL },
    { 0x828686ae4bf3b1e7ULL, 0x10a26e2069b47539ULL },
    { 0x354a4970c3798857ULL, 0x6617a2470d9126f2ULL },
    { 0x627399b8aeff223bULL, 0xc8cb1f7ff71d7799ULL },
    { 0xf5dc48949583aab8ULL, 0xf5da697d4650b5b4ULL },
    { 0x20b68ce2799464a4ULL, 0xaed50dfa8e1f2c0aULL },
    { 0x6ce16fa557508398ULL, 0x00621d3359224c5dULL },
    { 0xf5437e0d460fb080ULL, 0xdd0d3cab187ad8cdULL },
    { 0x5a6784625179eb67ULL, 0x0a7dff3ceca8340aULL },
    { 0x5bada798d37bf1daULL, 0x66c0ea498ae30744ULL },
    { 0xf49f3202af8b828dULL, 0x9649d0792a7c0f4dULL },
    { 0x06a6f88edfd5f696ULL, 0xdc21a06b0df2c4d7ULL },
    { 0xa958de14f8755f8eULL, 0x5c36949458eb5ff4ULL },
    { 0xf41b985d37852acaULL, 0x67840ae2d8980411ULL },
    { 0x0889eabc540cb5fcULL, 0x1002322c32d607c3ULL },
    { 0x2df8ffea481d069aULL, 0xd08621bb396c6752ULL },
    { 0x323e4d1e502fcc2eULL, 0xd677a3a5fbcf0701ULL },
    { 0x2a1b5a014f1e28d6ULL, 0x8fbc61e11fb583e8ULL },
    { 0x79920c49fc2295feULL, 0x1eb45493f04b9382ULL },
    { 0xa6f2980b77654919ULL, 0xd5706cc1e0b7274eULL },
    { 0x49242973f0d38bc5ULL, 0xbaae62ca0da195eeULL },
    { 0x5bb41874775c1cf5ULL, 0x9a1086f162376218ULL },
    { 0xa1c27475a00c0d56ULL, 0x5182ac806d5cecefULL },
    { 0xfa8abd84ca3bf283ULL, 0xfb2ef04531097121ULL },
    { 0xca58f43d9cb60c51ULL, 0xac19179c3d689655ULL },
    { 0x4a258cc0cdea0d50ULL, 0xed43af5dd7af7accULL },
    { 0xcc1823d552f2c042ULL, 0x468128efb2d5f96cULL },
    { 0xedc3c15e60d7a055ULL, 0xb25a4a5386305762ULL },
    { 0x9b3edfebd022f75fULL, 0x29041dcb27c38d24ULL },
    { 0xb4a161f08d70845aULL, 0xc64435382757e8c5ULL },
    { 0x1476f5e55753c1e6ULL, 0x66d8209886988ee9ULL },
};

__constant__ uint64_t XOR_HI[64][2] = {
    { 0x32b37ba8ebcebd21ULL, 0x19c7c3b6a2248bf0ULL },
    { 0xac20e08487f30521ULL, 0x04585f12f71a7a7eULL },
    { 0x3a04800b2f57d6c2ULL, 0x4361d754ffc3ef3aULL },
    { 0x9116ff8703a2fde4ULL, 0x94781ed65ce4f05cULL },
    { 0xc8bf201295aa0187ULL, 0x2dada86c4c4116a0ULL },
    { 0x169d177d72e1cf6eULL, 0x6190775f0a84b841ULL },
    { 0x6dc7ddf8aef47810ULL, 0x5bfa7ddac8c92335ULL },
    { 0xb2892a43430ef64eULL, 0xe0879f073b8390aaULL },
    { 0x935baac54121cfc4ULL, 0x9f0c3ea0adb5a286ULL },
    { 0x0ea8518bf879a5efULL, 0xf664d22a0895a881ULL },
    { 0x8ba56f98f2cc4f7aULL, 0xbf4ed50a596ac088ULL },
    { 0xeef21af06e0a0185ULL, 0xb003a9c42a64947fULL },
    { 0x9fb641777d5b0d1eULL, 0x63c0cd54965137beULL },
    { 0x0a06c702e598e322ULL, 0xfbeaf3c81b304e89ULL },
    { 0x1a5ba9c2663dd33fULL, 0x37d7f24be68d314fULL },
    { 0xbb27059694afd248ULL, 0x8d1ca37e6d9802f0ULL },
    { 0xfa53f67a95deccedULL, 0x84e7a1db22563dc3ULL },
    { 0xf6458473367f4bdaULL, 0xbb13ff967e38bc8cULL },
    { 0x6603c03ffc16d685ULL, 0x4f459c09fe3c94c6ULL },
    { 0x085404637907b059ULL, 0xbba841c7d6560f22ULL },
    { 0xbf5e11dcbb585d38ULL, 0xaa8bd2b5b259a49bULL },
    { 0x8be8ba0509a9e3b1ULL, 0xf4077d9579ed71c4ULL },
    { 0x6f1ee9bba817ede4ULL, 0x5a56ce904aadad43ULL },
    { 0x64bca8c9658ea667ULL, 0x85bd0a5b2b1f613eULL },
    { 0x9af56039ed5b676bULL, 0x7e8ced804a7bf3c1ULL },
    { 0x605b50115c8471b7ULL, 0xa2ec7b2e7c4cbcc9ULL },
    { 0x368afd852e5264eaULL, 0xec3d9bb94636cd36ULL },
    { 0xe492beffe8642a2bULL, 0x76982087ceeeab85ULL },
    { 0xb5395b14c2c37815ULL, 0x4801e76ae6fd6cbbULL },
    { 0xd9333173d2a5871eULL, 0x8d9d02574584a4bbULL },
    { 0x4b407a8076b89d2aULL, 0x4661b6902fc7d7beULL },
    { 0x0e50bff9ddccb272ULL, 0x570906b495b01afbULL },
    { 0xa05ac64529d282cfULL, 0xb5f8c537fd811c76ULL },
    { 0x4796788837961b7eULL, 0x8ceaa8fd64633a67ULL },
    { 0x826090d1fd6087b9ULL, 0x034ce4ff7e425e42ULL },
    { 0xce777c9e109b7000ULL, 0x18200d9679cc6824ULL },
    { 0x7f997981adf1dbf1ULL, 0xe7e04d380470b92cULL },
    { 0xfa5f51fac197f6abULL, 0x03f57d6bfc05bba6ULL },
    { 0x813e4a988e6ca307ULL, 0xc22968d5e4ce50c7ULL },
    { 0xa39f5ef8fd150cddULL, 0xe1c2bc69fe501b1eULL },
    { 0x8cb69e62422cb70eULL, 0xb4c2fbe82e04a84eULL },
    { 0x694ac8fda0855c93ULL, 0x87c0cdc2253a7269ULL },
    { 0xabaff55f08adaf63ULL, 0x066b779d29655731ULL },
    { 0x5fea359b6c9d1814ULL, 0x97bd79af0dfc9c0aULL },
    { 0x17e0a1707ea1201bULL, 0xf8a33459217404a2ULL },
    { 0x00122ea1784a4d19ULL, 0x00dd88bb0742b4e9ULL },
    { 0xa81623c9278d8a0dULL, 0xfc8078cedc62f651ULL },
    { 0xb5a03428ac00fd10ULL, 0x15cb73b6cbbff9d3ULL },
    { 0x308c609078741ef2ULL, 0x64ecd8db5c9ff721ULL },
    { 0xb3afddc877a91684ULL, 0xe8dd2d0bf421b74fULL },
    { 0xc4508328c345c028ULL, 0x566f3dbbc74afc1aULL },
    { 0x7b9d7f4dad12a68aULL, 0xf349042186423b7cULL },
    { 0x43f9aade34df2e55ULL, 0x6841f59bfec9c94bULL },
    { 0xc532c3fa0aadfa0bULL, 0xaffec201fcb72a07ULL },
    { 0xdd894f38e24ec479ULL, 0x6925e83b393340c1ULL },
    { 0x9815571c6837338cULL, 0x08ae87e6f15032e1ULL },
    { 0x2e34ccc955545d0dULL, 0x380e377d081e988aULL },
    { 0xabb6695499088738ULL, 0xfa489750820c64b1ULL },
    { 0xd1fc977813aa97f6ULL, 0x8e229c3a401cc040ULL },
    { 0xf33a77ab9e804e16ULL, 0x84fc15f114c8ffd5ULL },
    { 0x84774bf7aa07a202ULL, 0x5cbb3810d96db47aULL },
    { 0xabd2e2e4517a3793ULL, 0x61ba8c6be722c83bULL },
    { 0xcb76df4453ed7bf3ULL, 0xf958d44c7154bca9ULL },
    { 0x4955857644270dc4ULL, 0xba2b39a94a63bc6fULL },
};


__device__ inline uint64_t rotl64_d(uint64_t x, int k) {
    return (x << k) | (x >> (64 - k));
}

__device__ inline uint64_t mix64_d(uint64_t z) {
    z = (z ^ (z >> 30)) * XR_MIX1;
    z = (z ^ (z >> 27)) * XR_MIX2;
    return z ^ (z >> 31);
}

__device__ inline void xrsr_set_seed_d(uint64_t *lo, uint64_t *hi, uint64_t seed) {
    uint64_t v = seed ^ XR_SILVER;
    *lo = mix64_d(v);
    *hi = mix64_d(v + XR_GOLDEN);
    if ((*lo | *hi) == 0) {
        *lo = 0x9E6D62D0E69CFEEDULL;
        *hi = XR_SILVER;
    }
}

__device__ inline uint64_t xrsr_long_d(uint64_t *lo, uint64_t *hi) {
    uint64_t l = *lo, h = *hi;
    uint64_t result = rotl64_d(l + h, 17) + l;
    uint64_t hx = h ^ l;
    *lo = rotl64_d(l, 49) ^ hx ^ (hx << 21);
    *hi = rotl64_d(hx, 28);
    return result;
}

__device__ inline uint64_t xrsr_next_long_d(uint64_t *lo, uint64_t *hi) {
    int32_t hi32 = (int32_t)(xrsr_long_d(lo, hi) >> 32);
    int32_t lo32 = (int32_t)(xrsr_long_d(lo, hi) >> 32);
    int64_t result = ((int64_t)hi32 << 32) + (int64_t)lo32;
    return (uint64_t)result;
}

__device__ inline uint64_t decoration_seed_d(uint64_t world_seed, int32_t cx, int32_t cz) {
    uint64_t lo, hi;
    xrsr_set_seed_d(&lo, &hi, world_seed);
    uint64_t a = xrsr_next_long_d(&lo, &hi) | 1ULL;
    uint64_t b = xrsr_next_long_d(&lo, &hi) | 1ULL;
    int64_t bx = (int64_t)cx * 16;
    int64_t bz = (int64_t)cz * 16;
    return ((uint64_t)bx * a + (uint64_t)bz * b) ^ world_seed;
}

__device__ inline void skip_portal_d(uint64_t *lo, uint64_t *hi) {
    uint64_t l = *lo, h = *hi;
    uint64_t nl = 0, nh = 0;
    #pragma unroll
    for (int i = 0; i < 64; i++) {
        if ((l >> i) & 1ULL) { nh ^= XOR_LO[i][0]; nl ^= XOR_LO[i][1]; }
    }
    #pragma unroll
    for (int i = 0; i < 64; i++) {
        if ((h >> i) & 1ULL) { nh ^= XOR_HI[i][0]; nl ^= XOR_HI[i][1]; }
    }
    *lo = nl; *hi = nh;
}

// ─────────────────────────────────────────────────────────────────────
// Java Random (LCG, 48 bits) — for initFirstStronghold computation
// Ported from cubiomes/rng.h
// ─────────────────────────────────────────────────────────────────────

__device__ inline void java_set_seed(uint64_t *seed, uint64_t value) {
    *seed = (value ^ 0x5deece66dULL) & ((1ULL << 48) - 1);
}

__device__ inline int java_next(uint64_t *seed, const int bits) {
    *seed = (*seed * 0x5deece66dULL + 0xbULL) & ((1ULL << 48) - 1);
    return (int)((int64_t)*seed >> (48 - bits));
}

__device__ inline double java_next_double(uint64_t *seed) {
    uint64_t x = (uint64_t)java_next(seed, 26);
    x <<= 27;
    x += (uint64_t)(uint32_t)java_next(seed, 27);
    return (double)((int64_t)x) / (double)(1ULL << 53);
}

// Reproduces cubiomes' initFirstStronghold (Pos returned as packed int64).
// mc_version: 28 = 1.21.4 (>=MC_1_9 path). For now we just use that path
// since SSA defaults to 1.18+ algo.
__device__ inline void compute_first_stronghold_chunk(uint64_t world_seed,
                                                       int mc_version,
                                                       int32_t *out_cx,
                                                       int32_t *out_cz) {
    uint64_t rnds;
    java_set_seed(&rnds, world_seed);
    double angle = 2.0 * 3.141592653589793 * java_next_double(&rnds);
    double dist;
    // MC_1_9 in cubiomes header is the threshold (>= 1.9 = newer formula)
    // SSA values: 20=1.16, 21=1.17, 22=1.18, 24=1.19, 25=1.20, 26=1.21.1, 28=1.21.4
    // All >= 11 in cubiomes are >= 1.9 path
    if (mc_version >= 11) {
        dist = (4.0 * 32.0) + (java_next_double(&rnds) - 0.5) * 32.0 * 2.5;
    } else {
        dist = (1.25 + java_next_double(&rnds)) * 32.0;
    }
    int32_t bx = ((int32_t)round(cos(angle) * dist) * 16) + 8;
    int32_t bz = ((int32_t)round(sin(angle) * dist) * 16) + 8;
    *out_cx = bx >> 4;
    *out_cz = bz >> 4;
}

// Eye-RNG offset for the Xoroshiro decoration seed (1.18+). Mirrors the CPU
// eye_offset_xrsr / Python _eye_feature_offset:
//   1.18.0/1.18.1 (mc<=22): STRONGHOLDS step(5) idx0 -> +50000.
//   1.19+ (mc>=24): SURFACE_STRUCTURES step(4) idx19 (default, UNVERIFIED) -> +40019.
__device__ inline uint64_t eye_offset_xrsr_d(int mc_version) {
    return (mc_version <= 22) ? 50000ULL : (mc_version == 23) ? 40008ULL : 40019ULL;
}

// Test if seed gives 12/12 at given chunk via Gaider matrix.
// Early-exits on first failure. `off` = eye_offset_xrsr_d(mc_version).
__device__ inline bool gaider_check_12eye_d(uint64_t world_seed, int32_t cx, int32_t cz,
                                            uint64_t off) {
    uint64_t lo, hi;
    uint64_t deco = decoration_seed_d(world_seed, cx, cz);
    xrsr_set_seed_d(&lo, &hi, deco + off);
    skip_portal_d(&lo, &hi);
    #pragma unroll
    for (int i = 0; i < 12; i++) {
        uint32_t bits = (uint32_t)(xrsr_long_d(&lo, &hi) >> 40);
        // > 0.9 means bits > 0.9 * 2^24 = 15099494.4, so >= 15099495
        if (bits < 15099495u) return false;
    }
    return true;
}

// Same but counts eyes (0..12). Used for fast_mode 6-11.
__device__ inline int gaider_count_eyes_d(uint64_t world_seed, int32_t cx, int32_t cz,
                                          uint64_t off) {
    uint64_t lo, hi;
    uint64_t deco = decoration_seed_d(world_seed, cx, cz);
    xrsr_set_seed_d(&lo, &hi, deco + off);
    skip_portal_d(&lo, &hi);
    int eyes = 0;
    #pragma unroll
    for (int i = 0; i < 12; i++) {
        uint32_t bits = (uint32_t)(xrsr_long_d(&lo, &hi) >> 40);
        if (bits >= 15099495u) eyes++;
    }
    return eyes;
}

// ── MC 1.13-1.17 : pipeline LCG (Java Random) pour le comptage des yeux ──
// Copie bit-perfect du pipeline Python validé : JavaRandom(setDecorationSeed
// LCG + 50000), skip 760 nextFloat (prefixe PortalRoom, constant car
// checkAir=false), puis 12 nextFloat > 0.9. La validation CPU finale garantit
// l'exactitude ; ceci n'est qu'un pré-filtre (grille 15x15 + max).
__device__ inline int64_t java_next_long_d(uint64_t *seed) {
    int32_t hi = java_next(seed, 32);
    int32_t lo = java_next(seed, 32);
    uint64_t r = ((uint64_t)(int64_t)hi << 32) + (uint64_t)(int64_t)lo;
    return (int64_t)r;
}

__device__ inline uint64_t decoration_seed_lcg_d(uint64_t world_seed,
                                                 int32_t cx, int32_t cz) {
    uint64_t seed;
    java_set_seed(&seed, world_seed);
    uint64_t a = (uint64_t)java_next_long_d(&seed) | 1ULL;
    uint64_t b = (uint64_t)java_next_long_d(&seed) | 1ULL;
    uint64_t bx = (uint64_t)((int64_t)cx * 16);
    uint64_t bz = (uint64_t)((int64_t)cz * 16);
    return (bx * a + bz * b) ^ world_seed;
}

__device__ inline int gaider_count_eyes_lcg_d(uint64_t world_seed,
                                              int32_t cx, int32_t cz) {
    uint64_t deco = decoration_seed_lcg_d(world_seed, cx, cz);
    uint64_t seed;
    java_set_seed(&seed, deco + 50000ULL);
    // Saut O(1) du préfixe PortalRoom (= 760 pas LCG) au lieu d'une boucle.
    seed = (seed * 0xe5cfdccc10e1ULL + 0x2fc9e05b45b8ULL) & ((1ULL << 48) - 1);
    int eyes = 0;
    #pragma unroll
    for (int i = 0; i < 12; i++) {
        uint32_t bits = (uint32_t)java_next(&seed, 24);
        if (bits >= 15099495u) eyes++;
    }
    return eyes;
}

// ─────────────────────────────────────────────────────────────────────
// FULL GPU kernels — compute chunk AND scan in one kernel.
// No host-side initFirstStronghold needed → GPU saturated.
// ─────────────────────────────────────────────────────────────────────

// 12-eye scan: tests a 15×15 chunk grid around the init position to
// catch all possible portal_room locations. The init pos is approximate
// (±7 chunks) and the portal can be a few more chunks away from the
// stronghold start, so 15×15 covers the realistic range.
//
// This trades raw throughput (~225× fewer seeds/s in the worst case)
// for proper recall: every actual 12/12 seed is found.
__global__ void k_full_scan_12eye(int64_t seed_start, int32_t n,
                                    int mc_version,
                                    int64_t *out_seeds, int32_t *out_count,
                                    int32_t max_results) {
    int32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    int64_t seed = seed_start + (int64_t)i;
    int32_t cx0, cz0;
    compute_first_stronghold_chunk((uint64_t)seed, mc_version, &cx0, &cz0);
    uint64_t off = eye_offset_xrsr_d(mc_version);
    // Grid scan: ±7 chunks in each axis (15×15 = 225 tests per seed).
    // Early exit on first 12-match.
    bool found = false;
    for (int dx = -7; dx <= 7 && !found; dx++) {
        for (int dz = -7; dz <= 7 && !found; dz++) {
            bool _hit = (mc_version < 22)
                ? (gaider_count_eyes_lcg_d((uint64_t)seed, cx0 + dx, cz0 + dz) >= 12)
                : gaider_check_12eye_d((uint64_t)seed, cx0 + dx, cz0 + dz, off);
            if (_hit) {
                found = true;
            }
        }
    }
    if (found) {
        int32_t idx = atomicAdd(out_count, 1);
        if (idx < max_results) {
            out_seeds[idx] = seed;
        }
    }
}

__global__ void k_full_scan_min_eyes(int64_t seed_start, int32_t n,
                                       int mc_version, int min_eyes,
                                       int64_t *out_seeds, int32_t *out_eyes,
                                       int32_t *out_count, int32_t max_results) {
    int32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    int64_t seed = seed_start + (int64_t)i;
    int32_t cx0, cz0;
    compute_first_stronghold_chunk((uint64_t)seed, mc_version, &cx0, &cz0);
    uint64_t off = eye_offset_xrsr_d(mc_version);
    int best_eyes = 0;
    for (int dx = -7; dx <= 7; dx++) {
        for (int dz = -7; dz <= 7; dz++) {
            int eyes = (mc_version < 22)
                ? gaider_count_eyes_lcg_d((uint64_t)seed, cx0 + dx, cz0 + dz)
                : gaider_count_eyes_d((uint64_t)seed, cx0 + dx, cz0 + dz, off);
            if (eyes > best_eyes) {
                best_eyes = eyes;
                if (best_eyes >= 12) goto done;
            }
        }
    }
    done:
    if (best_eyes >= min_eyes) {
        int32_t idx = atomicAdd(out_count, 1);
        if (idx < max_results) {
            out_seeds[idx] = seed;
            out_eyes[idx] = best_eyes;
        }
    }
}

// Self-contained C-callable export: no host-side chunks needed.
extern "C" EXPORT int gaider_cuda_full_scan_12eye(
        int64_t seed_start, int32_t n, int mc_version,
        int64_t *out_seeds, int32_t max_results) {
    if (n <= 0) return 0;
    if (max_results < 1) max_results = 1;
    SsaDevBuf d_seeds, d_count;
    SSA_CK(d_seeds.alloc((size_t)max_results * sizeof(int64_t)));
    SSA_CK(d_count.alloc(sizeof(int32_t)));
    SSA_CK(cudaMemset(d_count.p, 0, sizeof(int32_t)));
    static SsaChunker ck(1 << 20, 1 << 12, 1 << 24);
    const int bs = 256;
    for (int32_t off = 0; off < n; ) {
        int32_t cur = (int32_t)((int64_t)(n - off) < ck.chunk ? (n - off) : ck.chunk);
        ck.begin();
        k_full_scan_12eye<<<(cur + bs - 1) / bs, bs>>>(
            seed_start + off, cur, mc_version, d_seeds.as<int64_t>(), d_count.as<int32_t>(), max_results);
        SSA_CK_LAUNCH();
        ck.end();
        off += cur;
    }
    int32_t h_count = 0;
    SSA_CK(cudaMemcpy(&h_count, d_count.p, sizeof(int32_t), cudaMemcpyDeviceToHost));
    int32_t actual = h_count < max_results ? h_count : max_results;
    if (actual > 0) {
        SSA_CK(cudaMemcpy(out_seeds, d_seeds.p, (size_t)actual * sizeof(int64_t), cudaMemcpyDeviceToHost));
    }
    return h_count;
}

extern "C" EXPORT int gaider_cuda_full_scan_min_eyes(
        int64_t seed_start, int32_t n, int mc_version, int min_eyes,
        int64_t *out_seeds, int32_t *out_eyes, int32_t max_results) {
    if (n <= 0) return 0;
    if (max_results < 1) max_results = 1;
    SsaDevBuf d_seeds, d_count, d_eyes;
    SSA_CK(d_seeds.alloc((size_t)max_results * sizeof(int64_t)));
    SSA_CK(d_eyes.alloc((size_t)max_results * sizeof(int32_t)));
    SSA_CK(d_count.alloc(sizeof(int32_t)));
    SSA_CK(cudaMemset(d_count.p, 0, sizeof(int32_t)));
    static SsaChunker ck(1 << 20, 1 << 12, 1 << 24);
    const int bs = 256;
    for (int32_t off = 0; off < n; ) {
        int32_t cur = (int32_t)((int64_t)(n - off) < ck.chunk ? (n - off) : ck.chunk);
        ck.begin();
        k_full_scan_min_eyes<<<(cur + bs - 1) / bs, bs>>>(
            seed_start + off, cur, mc_version, min_eyes,
            d_seeds.as<int64_t>(), d_eyes.as<int32_t>(), d_count.as<int32_t>(), max_results);
        SSA_CK_LAUNCH();
        ck.end();
        off += cur;
    }
    int32_t h_count = 0;
    SSA_CK(cudaMemcpy(&h_count, d_count.p, sizeof(int32_t), cudaMemcpyDeviceToHost));
    int32_t actual = h_count < max_results ? h_count : max_results;
    if (actual > 0) {
        SSA_CK(cudaMemcpy(out_seeds, d_seeds.p, (size_t)actual * sizeof(int64_t), cudaMemcpyDeviceToHost));
        SSA_CK(cudaMemcpy(out_eyes, d_eyes.p, (size_t)actual * sizeof(int32_t), cudaMemcpyDeviceToHost));
    }
    return h_count;
}

// ─────────────────────────────────────────────────────────────────────
// LAYOUT-BASED scan (Gaider10 style)
//
// Architecture: CPU (via libgaider_layouts) generates Layouts:
//   {struct_seed, start_chunk, portal_chunk}
// where struct_seed is the LOW 48 bits of world_seed.
// For each layout, we test all 2^16 = 65536 high-bit variations of the
// world_seed against the EXACT portal_chunk (no grid scan).
//
// This gives ~225× more efficiency than the grid scan version since we
// know exactly where the portal is.
// ─────────────────────────────────────────────────────────────────────

struct LayoutInput {
    uint64_t structure_seed;
    int16_t start_chunk_x;
    int16_t portal_chunk_x;
    int16_t start_chunk_z;
    int16_t portal_chunk_z;
};

// One thread per (layout, high16). Tests Gaider 12-eye check at the
// exact portal chunk known from layout. Massively more efficient than
// the grid scan because we have the precise chunk.
__global__ void k_layout_scan_12eye(
        const LayoutInput *layouts, uint32_t n_layouts, int mc_version,
        int64_t *out_seeds, int32_t *out_count, int32_t max_results) {
    uint32_t tid = blockIdx.x * blockDim.x + threadIdx.x;
    uint32_t total_threads = (uint32_t)n_layouts * 65536u;
    if (tid >= total_threads) return;

    uint32_t layout_idx = tid >> 16;       // /65536
    uint32_t high16 = tid & 0xFFFFu;       // %65536

    LayoutInput L = layouts[layout_idx];
    uint64_t world_seed = ((uint64_t)high16 << 48) | L.structure_seed;

    bool _ok = (mc_version < 22)
        ? (gaider_count_eyes_lcg_d(world_seed, L.portal_chunk_x, L.portal_chunk_z) >= 12)
        : gaider_check_12eye_d(world_seed, L.portal_chunk_x, L.portal_chunk_z,
                               eye_offset_xrsr_d(mc_version));
    if (_ok) {
        int32_t idx = atomicAdd(out_count, 1);
        if (idx < max_results) {
            out_seeds[idx] = (int64_t)world_seed;
        }
    }
}

// Same but for arbitrary min_eyes
__global__ void k_layout_scan_min_eyes(
        const LayoutInput *layouts, uint32_t n_layouts, int min_eyes, int mc_version,
        int64_t *out_seeds, int32_t *out_eyes, int32_t *out_count, int32_t max_results) {
    uint32_t tid = blockIdx.x * blockDim.x + threadIdx.x;
    uint32_t total_threads = (uint32_t)n_layouts * 65536u;
    if (tid >= total_threads) return;

    uint32_t layout_idx = tid >> 16;
    uint32_t high16 = tid & 0xFFFFu;

    LayoutInput L = layouts[layout_idx];
    uint64_t world_seed = ((uint64_t)high16 << 48) | L.structure_seed;

    int eyes = (mc_version < 22)
        ? gaider_count_eyes_lcg_d(world_seed, L.portal_chunk_x, L.portal_chunk_z)
        : gaider_count_eyes_d(world_seed, L.portal_chunk_x, L.portal_chunk_z,
                              eye_offset_xrsr_d(mc_version));
    if (eyes >= min_eyes) {
        int32_t idx = atomicAdd(out_count, 1);
        if (idx < max_results) {
            out_seeds[idx] = (int64_t)world_seed;
            out_eyes[idx] = eyes;
        }
    }
}

// C-callable: takes a host buffer of layouts, scans them on GPU.
// Returns number of matches found (capped to max_results in output).
extern "C" EXPORT int gaider_cuda_layout_scan_12eye(
        const LayoutInput *layouts, uint32_t n_layouts, int mc_version,
        int64_t *out_seeds, int32_t max_results) {
    if (n_layouts == 0) return 0;
    if (max_results < 1) max_results = 1;
    SsaDevBuf d_seeds, d_count;
    SSA_CK(d_seeds.alloc((size_t)max_results * sizeof(int64_t)));
    SSA_CK(d_count.alloc(sizeof(int32_t)));
    SSA_CK(cudaMemset(d_count.p, 0, sizeof(int32_t)));
    SsaDevBuf d_layouts;
    SSA_CK(d_layouts.alloc((size_t)n_layouts * sizeof(LayoutInput)));
    SSA_CK(cudaMemcpy(d_layouts.p, layouts, (size_t)n_layouts * sizeof(LayoutInput), cudaMemcpyHostToDevice));
    static SsaChunker ck(256, 1, 16384);          // layouts par lancement (×65536 threads)
    const int bs = 256;
    for (uint32_t off = 0; off < n_layouts; ) {
        uint32_t cur = (uint32_t)((int64_t)(n_layouts - off) < ck.chunk ? (n_layouts - off) : ck.chunk);
        uint64_t threads = (uint64_t)cur * 65536ULL;
        ck.begin();
        k_layout_scan_12eye<<<(unsigned)((threads + bs - 1) / bs), bs>>>(
            d_layouts.as<LayoutInput>() + off, cur, mc_version,
            d_seeds.as<int64_t>(), d_count.as<int32_t>(), max_results);
        SSA_CK_LAUNCH();
        ck.end();
        off += cur;
    }
    int32_t h_count = 0;
    SSA_CK(cudaMemcpy(&h_count, d_count.p, sizeof(int32_t), cudaMemcpyDeviceToHost));
    int32_t actual = h_count < max_results ? h_count : max_results;
    if (actual > 0) {
        SSA_CK(cudaMemcpy(out_seeds, d_seeds.p, (size_t)actual * sizeof(int64_t), cudaMemcpyDeviceToHost));
    }
    return h_count;
}

extern "C" EXPORT int gaider_cuda_layout_scan_min_eyes(
        const LayoutInput *layouts, uint32_t n_layouts, int min_eyes, int mc_version,
        int64_t *out_seeds, int32_t *out_eyes, int32_t max_results) {
    if (n_layouts == 0) return 0;
    if (max_results < 1) max_results = 1;
    SsaDevBuf d_seeds, d_count, d_eyes;
    SSA_CK(d_seeds.alloc((size_t)max_results * sizeof(int64_t)));
    SSA_CK(d_eyes.alloc((size_t)max_results * sizeof(int32_t)));
    SSA_CK(d_count.alloc(sizeof(int32_t)));
    SSA_CK(cudaMemset(d_count.p, 0, sizeof(int32_t)));
    SsaDevBuf d_layouts;
    SSA_CK(d_layouts.alloc((size_t)n_layouts * sizeof(LayoutInput)));
    SSA_CK(cudaMemcpy(d_layouts.p, layouts, (size_t)n_layouts * sizeof(LayoutInput), cudaMemcpyHostToDevice));
    static SsaChunker ck(256, 1, 16384);          // layouts par lancement (×65536 threads)
    const int bs = 256;
    for (uint32_t off = 0; off < n_layouts; ) {
        uint32_t cur = (uint32_t)((int64_t)(n_layouts - off) < ck.chunk ? (n_layouts - off) : ck.chunk);
        uint64_t threads = (uint64_t)cur * 65536ULL;
        ck.begin();
        k_layout_scan_min_eyes<<<(unsigned)((threads + bs - 1) / bs), bs>>>(
            d_layouts.as<LayoutInput>() + off, cur, min_eyes, mc_version,
            d_seeds.as<int64_t>(), d_eyes.as<int32_t>(), d_count.as<int32_t>(), max_results);
        SSA_CK_LAUNCH();
        ck.end();
        off += cur;
    }
    int32_t h_count = 0;
    SSA_CK(cudaMemcpy(&h_count, d_count.p, sizeof(int32_t), cudaMemcpyDeviceToHost));
    int32_t actual = h_count < max_results ? h_count : max_results;
    if (actual > 0) {
        SSA_CK(cudaMemcpy(out_seeds, d_seeds.p, (size_t)actual * sizeof(int64_t), cudaMemcpyDeviceToHost));
        SSA_CK(cudaMemcpy(out_eyes, d_eyes.p, (size_t)actual * sizeof(int32_t), cudaMemcpyDeviceToHost));
    }
    return h_count;
}

// ─────────────────────────────────────────────────────────────────────
// SEED-LIST / all-128 eye finder (GPU eye-count over CPU-snapped positions).
// The CPU computes the real (biome-snapped) stronghold positions; the GPU just
// does the heavy eye-count: per entry, the MAX single-chunk eye count over a
// (2*grid+1)^2 window around (cxs[i],czs[i]). out_eyes[i] = that max. Exact for
// the 12-eye any-chunk rule (the window covers every chunk the portal touches);
// the CPU re-validates survivors exactly. Version-correct (LCG <1.18, Xrsr 1.18+).
__global__ void k_eyes_arr(const int64_t *seeds, const int32_t *cxs,
                           const int32_t *czs, int32_t n, int mc_version,
                           int grid, int32_t *out_eyes) {
    int32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    uint64_t seed = (uint64_t)seeds[i];
    int cx0 = cxs[i], cz0 = czs[i];
    uint64_t off = eye_offset_xrsr_d(mc_version);
    int best = 0;
    for (int dx = -grid; dx <= grid && best < 12; dx++) {
        for (int dz = -grid; dz <= grid && best < 12; dz++) {
            int e = (mc_version < 22)
                ? gaider_count_eyes_lcg_d(seed, cx0 + dx, cz0 + dz)
                : gaider_count_eyes_d(seed, cx0 + dx, cz0 + dz, off);
            if (e > best) best = e;
        }
    }
    out_eyes[i] = best;
}

// out_eyes[i] (length n) gets the grid-max eye count for (seeds[i], cxs[i], czs[i]).
extern "C" EXPORT int gaider_cuda_eyes_arr(
        const int64_t *seeds, const int32_t *cxs, const int32_t *czs,
        int32_t n, int mc_version, int grid, int32_t *out_eyes) {
    if (n <= 0) return 0;
    SsaDevBuf d_seeds, d_cx, d_cz, d_out;
    SSA_CK(d_seeds.alloc((size_t)n * sizeof(int64_t)));
    SSA_CK(d_cx.alloc((size_t)n * sizeof(int32_t)));
    SSA_CK(d_cz.alloc((size_t)n * sizeof(int32_t)));
    SSA_CK(d_out.alloc((size_t)n * sizeof(int32_t)));
    SSA_CK(cudaMemcpy(d_seeds.p, seeds, (size_t)n * sizeof(int64_t), cudaMemcpyHostToDevice));
    SSA_CK(cudaMemcpy(d_cx.p, cxs, (size_t)n * sizeof(int32_t), cudaMemcpyHostToDevice));
    SSA_CK(cudaMemcpy(d_cz.p, czs, (size_t)n * sizeof(int32_t), cudaMemcpyHostToDevice));
    static SsaChunker ck(1 << 18, 1 << 10, 1 << 24);
    const int bs = 256;
    for (int32_t off = 0; off < n; ) {
        int32_t cur = (int32_t)((int64_t)(n - off) < ck.chunk ? (n - off) : ck.chunk);
        ck.begin();
        k_eyes_arr<<<(cur + bs - 1) / bs, bs>>>(d_seeds.as<int64_t>() + off, d_cx.as<int32_t>() + off,
            d_cz.as<int32_t>() + off, cur, mc_version, grid, d_out.as<int32_t>() + off);
        SSA_CK_LAUNCH();
        ck.end();
        off += cur;
    }
    SSA_CK(cudaMemcpy(out_eyes, d_out.p, (size_t)n * sizeof(int32_t), cudaMemcpyDeviceToHost));
    return n;
}

// ─────────────────────────────────────────────────────────────────────
// Old kernels (kept for fallback compatibility)
// ─────────────────────────────────────────────────────────────────────

// Each thread tests ONE seed against the 12-eye check.
// Matches are atomically appended to out_seeds[] (capacity max_results).
__global__ void k_scan_12eye(int64_t seed_start, int32_t n,
                              const int32_t *cx_arr, const int32_t *cz_arr,
                              int64_t *out_seeds, int32_t *out_count,
                              int32_t max_results) {
    int32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    int64_t seed = seed_start + (int64_t)i;
    int32_t cx = cx_arr[i];
    int32_t cz = cz_arr[i];
    // Legacy kernel (no mc_version; not used by the live path, which uses the
    // version-aware k_full_scan_* / k_layout_scan_*). Keep the old +40019.
    if (gaider_check_12eye_d((uint64_t)seed, cx, cz, 40019ULL)) {
        int32_t idx = atomicAdd(out_count, 1);
        if (idx < max_results) {
            out_seeds[idx] = seed;
        }
    }
}

// Same for arbitrary min_eyes (6-11).
__global__ void k_scan_min_eyes(int64_t seed_start, int32_t n,
                                  const int32_t *cx_arr, const int32_t *cz_arr,
                                  int min_eyes,
                                  int64_t *out_seeds, int32_t *out_eyes,
                                  int32_t *out_count, int32_t max_results) {
    int32_t i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    int64_t seed = seed_start + (int64_t)i;
    int32_t cx = cx_arr[i];
    int32_t cz = cz_arr[i];
    // Legacy kernel (see note in k_scan_12eye): keep the old +40019.
    int eyes = gaider_count_eyes_d((uint64_t)seed, cx, cz, 40019ULL);
    if (eyes >= min_eyes) {
        int32_t idx = atomicAdd(out_count, 1);
        if (idx < max_results) {
            out_seeds[idx] = seed;
            out_eyes[idx] = eyes;
        }
    }
}

// ─────────────────────────────────────────────────────────────────────
// C-callable API
// ─────────────────────────────────────────────────────────────────────

// Scan N seeds for 12/12 matches.
// `cx_arr`/`cz_arr` are pre-computed init chunks (length n) on host.
// out_seeds is filled with up to max_results matches.
// Returns number of matches found (may exceed max_results in atomic counter).
extern "C" EXPORT int gaider_cuda_scan_12eye(
        int64_t seed_start, int32_t n,
        const int32_t *cx_arr, const int32_t *cz_arr,
        int64_t *out_seeds, int32_t max_results) {
    if (n <= 0) return 0;
    SsaDevBuf d_cx, d_cz;
    SSA_CK(d_cx.alloc((size_t)n * sizeof(int32_t)));
    SSA_CK(d_cz.alloc((size_t)n * sizeof(int32_t)));
    SSA_CK(cudaMemcpy(d_cx.p, cx_arr, (size_t)n * sizeof(int32_t), cudaMemcpyHostToDevice));
    SSA_CK(cudaMemcpy(d_cz.p, cz_arr, (size_t)n * sizeof(int32_t), cudaMemcpyHostToDevice));
    if (max_results < 1) max_results = 1;
    SsaDevBuf d_seeds, d_count;
    SSA_CK(d_seeds.alloc((size_t)max_results * sizeof(int64_t)));
    SSA_CK(d_count.alloc(sizeof(int32_t)));
    SSA_CK(cudaMemset(d_count.p, 0, sizeof(int32_t)));
    const int bs = 256;
    k_scan_12eye<<<(n + bs - 1) / bs, bs>>>(seed_start, n, d_cx.as<int32_t>(), d_cz.as<int32_t>(),
        d_seeds.as<int64_t>(), d_count.as<int32_t>(), max_results);
    SSA_CK_LAUNCH();
    int32_t h_count = 0;
    SSA_CK(cudaMemcpy(&h_count, d_count.p, sizeof(int32_t), cudaMemcpyDeviceToHost));
    int32_t actual = h_count < max_results ? h_count : max_results;
    if (actual > 0) {
        SSA_CK(cudaMemcpy(out_seeds, d_seeds.p, (size_t)actual * sizeof(int64_t), cudaMemcpyDeviceToHost));
    }
    return h_count;
}

extern "C" EXPORT int gaider_cuda_scan_min_eyes(
        int64_t seed_start, int32_t n,
        const int32_t *cx_arr, const int32_t *cz_arr,
        int min_eyes,
        int64_t *out_seeds, int32_t *out_eyes, int32_t max_results) {
    if (n <= 0) return 0;
    SsaDevBuf d_cx, d_cz;
    SSA_CK(d_cx.alloc((size_t)n * sizeof(int32_t)));
    SSA_CK(d_cz.alloc((size_t)n * sizeof(int32_t)));
    SSA_CK(cudaMemcpy(d_cx.p, cx_arr, (size_t)n * sizeof(int32_t), cudaMemcpyHostToDevice));
    SSA_CK(cudaMemcpy(d_cz.p, cz_arr, (size_t)n * sizeof(int32_t), cudaMemcpyHostToDevice));
    if (max_results < 1) max_results = 1;
    SsaDevBuf d_seeds, d_count, d_eyes;
    SSA_CK(d_seeds.alloc((size_t)max_results * sizeof(int64_t)));
    SSA_CK(d_eyes.alloc((size_t)max_results * sizeof(int32_t)));
    SSA_CK(d_count.alloc(sizeof(int32_t)));
    SSA_CK(cudaMemset(d_count.p, 0, sizeof(int32_t)));
    const int bs = 256;
    k_scan_min_eyes<<<(n + bs - 1) / bs, bs>>>(seed_start, n, d_cx.as<int32_t>(), d_cz.as<int32_t>(), min_eyes,
        d_seeds.as<int64_t>(), d_eyes.as<int32_t>(), d_count.as<int32_t>(), max_results);
    SSA_CK_LAUNCH();
    int32_t h_count = 0;
    SSA_CK(cudaMemcpy(&h_count, d_count.p, sizeof(int32_t), cudaMemcpyDeviceToHost));
    int32_t actual = h_count < max_results ? h_count : max_results;
    if (actual > 0) {
        SSA_CK(cudaMemcpy(out_seeds, d_seeds.p, (size_t)actual * sizeof(int64_t), cudaMemcpyDeviceToHost));
        SSA_CK(cudaMemcpy(out_eyes, d_eyes.p, (size_t)actual * sizeof(int32_t), cudaMemcpyDeviceToHost));
    }
    return h_count;
}

// Probe: returns 1 if at least one CUDA-capable device is available.
extern "C" EXPORT int gaider_cuda_lcg_marker() { return 1; }

extern "C" EXPORT int gaider_cuda_available() {
    static int cached = -1;
    if (cached < 0) cached = (ssa_cuda_selftest() == 0) ? 1 : 0;
    return cached;
}


// ═════════════════════════════════════════════════════════════════════════════
// CRUNCHINESS — yo-score pre-filter (ported from COMMISSION by gaider_ssa)
//
// Filters seeds by checking the yo-offsets of a chosen climate noise.
// Seeds where the fractional parts of yo are close to 0.5 for the dominant
// octave pairs are unlikely to produce extreme values for that climate, so we
// reject them cheaply before any full noise evaluation.
//
// The math (Commission weighting, 3 dominant octave pairs):
//   score = 0.35*|{yo_0A}-0.5| + 0.35*|{yo_0B}-0.5|
//         + 0.11*|{yo_1A}-0.5| + 0.11*|{yo_1B}-0.5|
//         + 0.04*|{yo_2A}-0.5| + 0.04*|{yo_2B}-0.5|
//
//   A seed is kept only when score < threshold (default 0.035 → ~1 in 9400).
//
// Supported climate_id values (match Python criteria.py enum):
//   0 = Temperature     (hash: minecraft:temperature,  first_octave -10)
//   1 = Humidity        (hash: minecraft:vegetation,   first_octave -8 )
//   2 = Continentalness (hash: minecraft:continentalness, first_octave -9)
//   3 = Erosion         (hash: minecraft:erosion,      first_octave -9 )
//   5 = Weirdness       (hash: minecraft:ridge,        first_octave -7 )
//
// XrsrRandom / fork constants are bit-perfect with Minecraft 1.18+ and
// match the implementation in COMMISSION's gpu.cu / Random.h.
// ═════════════════════════════════════════════════════════════════════════════

// ── Xoshiro-128** state helpers ──────────────────────────────────────────────

// The fork mechanism used by Minecraft's WorldgenRandom (XoroshiroRandom).
// Constants taken from COMMISSION/src/gpu.cu (which itself mirrors cubiomes).
#define XR_SILVER_C 0x6A09E667F3BCC909ULL
#define XR_GOLDEN_C 0x9E3779B97F4A7C15ULL
#define XR_MIX1_C   0xBF58476D1CE4E5B9ULL
#define XR_MIX2_C   0x94D049BB133111EBULL

__device__ __forceinline__ uint64_t cr_mix64(uint64_t z) {
    z = (z ^ (z >> 30)) * XR_MIX1_C;
    z = (z ^ (z >> 27)) * XR_MIX2_C;
    return z ^ (z >> 31);
}

__device__ __forceinline__ void cr_set_seed(uint64_t *lo, uint64_t *hi,
                                             uint64_t seed) {
    uint64_t v = seed ^ XR_SILVER_C;
    *lo = cr_mix64(v);
    *hi = cr_mix64(v + XR_GOLDEN_C);
    if ((*lo | *hi) == 0ULL) {
        *lo = 0x9E6D62D0E69CFEEDULL;
        *hi = XR_SILVER_C;
    }
}

__device__ __forceinline__ uint64_t cr_next_long(uint64_t *lo, uint64_t *hi) {
    uint64_t l = *lo, h = *hi;
    uint64_t result = ((l + h) << 17 | (l + h) >> 47) + l;
    uint64_t hx = h ^ l;
    *lo = (l << 49 | l >> 15) ^ hx ^ (hx << 21);
    *hi = (hx << 28 | hx >> 36);
    return result;
}

// cr_fork_from removed — crunch_stream_roots uses direct XOR (no fork needed)

// cr_octave_yfrac: exact port of crunch_yfrac_for_octave() from session_engine.c
//
//   xr.lo = root_lo ^ md5_octave[hidx][0]
//   xr.hi = root_hi ^ md5_octave[hidx][1]
//   xNextLong(&xr)            // skip "a"
//   b_raw = xNextLong(&xr)    // read "b"
//   bits45 = (b_raw >> 11) & ((1<<45)-1)
//   y_frac = bits45 * 2^-45
//
// root_lo/root_hi = the stream root (a_lo/a_hi OR b_lo/b_hi from cr_stream_roots)
// oct_hash_lo/hi  = md5_octave_n[hidx]
__device__ __forceinline__ float cr_octave_yfrac(
        uint64_t root_lo, uint64_t root_hi,
        uint64_t oct_hash_lo, uint64_t oct_hash_hi) {
    // xr = root ^ octave_hash
    uint64_t xr_lo = root_lo ^ oct_hash_lo;
    uint64_t xr_hi = root_hi ^ oct_hash_hi;
    // skip "a" = one xNextLong
    cr_next_long(&xr_lo, &xr_hi);
    // read "b" = second xNextLong
    uint64_t b_raw = cr_next_long(&xr_lo, &xr_hi);
    // y_frac = ((b_raw >> 11) & ((1<<45)-1)) * 2^-45
    uint64_t bits45 = (b_raw >> 11) & ((1ULL << 45) - 1);
    return (float)((double)bits45 * (1.0 / (double)(1ULL << 45)));
}

// ── Climate hash + octave lookup tables ──────────────────────────────────────
//
// climate_id:  0=Temperature  1=Humidity  2=Continentalness  3=Erosion  5=Weirdness
//
// Climate hashes (from COMMISSION/src/gpu.cu + cubiomes/biomenoise.c):
//   minecraft:temperature     → 0x5c7e6b29735f0d7f / 0xf7d86f1bbc734988  first_octave=-10
//   minecraft:vegetation      → 0x81bb4d22e8dc168e / 0xf1c8b4bea16303cd  first_octave=-8
//   minecraft:continentalness → 0x83886c9d0ae3a662 / 0xafa638a61b42e8ad  first_octave=-9
//   minecraft:erosion         → 0xd02491e6058f6fd8 / 0x4792512c94c17a80  first_octave=-9
//   minecraft:ridge (weird.)  → 0xefc8ef4d36102b34 / 0x1beeeb324a0f24ea  first_octave=-7
//
// octave hashes from hash_octave[] table (index = first_octave + 12):
//   octave_-10 → index 2 → {0x36d326eed40efeb2, 0x5be9ce18223c636a}
//   octave_-9  → index 3 → {0x082fe255f8be6631, 0x4e96119e22dedc81}
//   octave_-8  → index 4 → {0x0ef68ec68504005e, 0x48b6bf93a2789640}
//   octave_-7  → index 5 → {0xf11268128982754f, 0x257a1d670430b0aa}
//   octave_-6  → index 6 → {0xe51c98ce7d1de664, 0x5f9478a733040c45}

// Per-climate data packed as: {hash_lo, hash_hi, oct0_lo, oct0_hi, oct1_lo, oct1_hi, oct2_lo, oct2_hi}
// Index mapping: 0=Temp, 1=Hum, 2=Cont, 3=Ero, 4=unused(depth), 5=Weird
__constant__ uint64_t cr_climate_data[6][8] = {
    // 0: Temperature  (first_octave=-10 → octaves -10,-9,-8)
    { 0x5c7e6b29735f0d7fULL, 0xf7d86f1bbc734988ULL,
      0x36d326eed40efeb2ULL, 0x5be9ce18223c636aULL,  // oct -10
      0x082fe255f8be6631ULL, 0x4e96119e22dedc81ULL,  // oct -9
      0x0ef68ec68504005eULL, 0x48b6bf93a2789640ULL   // oct -8
    },
    // 1: Humidity  (first_octave=-8 → octaves -8,-7,-6)
    { 0x81bb4d22e8dc168eULL, 0xf1c8b4bea16303cdULL,
      0x0ef68ec68504005eULL, 0x48b6bf93a2789640ULL,  // oct -8
      0xf11268128982754fULL, 0x257a1d670430b0aaULL,  // oct -7
      0xe51c98ce7d1de664ULL, 0x5f9478a733040c45ULL   // oct -6
    },
    // 2: Continentalness  (first_octave=-9 → octaves -9,-8,-7)
    { 0x83886c9d0ae3a662ULL, 0xafa638a61b42e8adULL,
      0x082fe255f8be6631ULL, 0x4e96119e22dedc81ULL,  // oct -9
      0x0ef68ec68504005eULL, 0x48b6bf93a2789640ULL,  // oct -8
      0xf11268128982754fULL, 0x257a1d670430b0aaULL   // oct -7
    },
    // 3: Erosion  (first_octave=-9 → octaves -9,-8,-7)
    { 0xd02491e6058f6fd8ULL, 0x4792512c94c17a80ULL,
      0x082fe255f8be6631ULL, 0x4e96119e22dedc81ULL,  // oct -9
      0x0ef68ec68504005eULL, 0x48b6bf93a2789640ULL,  // oct -8
      0xf11268128982754fULL, 0x257a1d670430b0aaULL   // oct -7
    },
    // 4: (depth/shift — unused, not supported)
    { 0, 0,  0, 0,  0, 0,  0, 0 },
    // 5: Weirdness  (first_octave=-7 → octaves -7,-6,-5)
    { 0xefc8ef4d36102b34ULL, 0x1beeeb324a0f24eaULL,
      0xf11268128982754fULL, 0x257a1d670430b0aaULL,  // oct -7
      0xe51c98ce7d1de664ULL, 0x5f9478a733040c45ULL,  // oct -6
      0x6d7b49e7e429850aULL, 0x2e3063c622a24777ULL   // oct -5
    },
};

// ── Main crunchiness kernel ───────────────────────────────────────────────────
//
// Each thread handles one seed in [seed_start, seed_start+n).
// climate_id selects which noise to test (0=Temp,1=Hum,2=Cont,3=Ero,5=Weird).
// Seeds that pass the yo-score threshold are written to out_seeds.

__global__ void k_crunchiness_filter(
        int64_t seed_start, int32_t n,
        int32_t climate_id,
        float threshold,
        int32_t mode_flag,          // 0 = crunchy (score < threshold), 1 = bland (score >= threshold)
        int64_t *out_seeds, int32_t *out_count, int32_t max_results) {

    int32_t idx = blockIdx.x * blockDim.x + threadIdx.x;
    if (idx >= n) return;

    uint64_t seed = (uint64_t)((int64_t)seed_start + idx);

    // ── Exact port of crunch_stream_roots() + compute_crunchiness_score() ──
    // from session_engine.c — bit-perfect with the CPU evaluator.
    //
    // crunch_stream_roots:
    //   xSetSeed(seed) → xlo = xNextLong, xhi = xNextLong
    //   noise_random = (xlo ^ climate_hash_lo, xhi ^ climate_hash_hi)
    //   a_lo = xNextLong(&noise_random); a_hi = xNextLong(&noise_random)
    //   b_lo = xNextLong(&noise_random); b_hi = xNextLong(&noise_random)

    int cid = (climate_id >= 0 && climate_id <= 5) ? climate_id : 2;

    uint64_t pxr_lo, pxr_hi;
    cr_set_seed(&pxr_lo, &pxr_hi, seed);
    uint64_t xlo = cr_next_long(&pxr_lo, &pxr_hi);
    uint64_t xhi = cr_next_long(&pxr_lo, &pxr_hi);

    // noise_random = xlo^climate_lo, xhi^climate_hi  (direct XOR, no fork())
    uint64_t nr_lo = xlo ^ cr_climate_data[cid][0];
    uint64_t nr_hi = xhi ^ cr_climate_data[cid][1];

    // Stream roots: 2 consecutive xNextLong pairs from noise_random
    uint64_t a_lo = cr_next_long(&nr_lo, &nr_hi);
    uint64_t a_hi = cr_next_long(&nr_lo, &nr_hi);
    uint64_t b_lo = cr_next_long(&nr_lo, &nr_hi);
    uint64_t b_hi = cr_next_long(&nr_lo, &nr_hi);

    // Per-octave y_frac via cr_octave_yfrac (mirrors crunch_yfrac_for_octave)
    float yo_0A = cr_octave_yfrac(a_lo, a_hi,
                                   cr_climate_data[cid][2], cr_climate_data[cid][3]);
    float yo_1A = cr_octave_yfrac(a_lo, a_hi,
                                   cr_climate_data[cid][4], cr_climate_data[cid][5]);
    float yo_2A = cr_octave_yfrac(a_lo, a_hi,
                                   cr_climate_data[cid][6], cr_climate_data[cid][7]);

    float yo_0B = cr_octave_yfrac(b_lo, b_hi,
                                   cr_climate_data[cid][2], cr_climate_data[cid][3]);
    float yo_1B = cr_octave_yfrac(b_lo, b_hi,
                                   cr_climate_data[cid][4], cr_climate_data[cid][5]);
    float yo_2B = cr_octave_yfrac(b_lo, b_hi,
                                   cr_climate_data[cid][6], cr_climate_data[cid][7]);

    // Commission weighting — matches test_crunchiness() in session_engine.c.
    // score = sum(weight * |y - 0.5|)  — NO *2, matching the C engine exactly.
    float score =
        0.35f * fabsf(yo_0A - 0.5f) + 0.35f * fabsf(yo_0B - 0.5f) +
        0.11f * fabsf(yo_1A - 0.5f) + 0.11f * fabsf(yo_1B - 0.5f) +
        0.04f * fabsf(yo_2A - 0.5f) + 0.04f * fabsf(yo_2B - 0.5f);

    // mode_flag 0 = crunchy: keep seeds where score < threshold
    // mode_flag 1 = bland:   keep seeds where score >= threshold
    bool is_crunchy = (score < threshold);
    if (mode_flag == 0 && !is_crunchy) return;  // want crunchy, seed is bland → reject
    if (mode_flag == 1 &&  is_crunchy) return;  // want bland,   seed is crunchy → reject

    // ── Step 7: write result ──────────────────────────────────────────────
    int32_t pos = atomicAdd(out_count, 1);
    if (pos < max_results)
        out_seeds[pos] = (int64_t)seed;
}

// ── C-callable exports ────────────────────────────────────────────────────────
//
// gaider_cuda_crunchiness_filter(seed_start, n, threshold,
//                                out_seeds, max_results) -> int
//   Backward-compatible: uses continentalness (climate_id=2).
//
// gaider_cuda_crunchiness_filter_ex(seed_start, n, climate_id, threshold,
//                                   out_seeds, max_results) -> int
//   Extended: selects climate by id (0=Temp,1=Hum,2=Cont,3=Ero,5=Weird).

extern "C" EXPORT int gaider_cuda_crunchiness_filter(
        int64_t seed_start, int32_t n,
        float threshold,
        int64_t *out_seeds, int32_t max_results) {
    if (n <= 0) return 0;
    if (max_results < 1) max_results = 1;
    SsaDevBuf d_seeds, d_count;
    SSA_CK(d_seeds.alloc((size_t)max_results * sizeof(int64_t)));
    SSA_CK(d_count.alloc(sizeof(int32_t)));
    SSA_CK(cudaMemset(d_count.p, 0, sizeof(int32_t)));
    static SsaChunker ck(1 << 22, 1 << 14, 1 << 27);
    const int bs = 512;
    for (int32_t off = 0; off < n; ) {
        int32_t cur = (int32_t)((int64_t)(n - off) < ck.chunk ? (n - off) : ck.chunk);
        ck.begin();
        k_crunchiness_filter<<<(cur + bs - 1) / bs, bs>>>(
            seed_start + off, cur, 2, threshold, 0,
            d_seeds.as<int64_t>(), d_count.as<int32_t>(), max_results);
        SSA_CK_LAUNCH();
        ck.end();
        off += cur;
    }
    int32_t h_count = 0;
    SSA_CK(cudaMemcpy(&h_count, d_count.p, sizeof(int32_t), cudaMemcpyDeviceToHost));
    int32_t actual = h_count < max_results ? h_count : max_results;
    if (actual > 0) {
        SSA_CK(cudaMemcpy(out_seeds, d_seeds.p, (size_t)actual * sizeof(int64_t), cudaMemcpyDeviceToHost));
    }
    return h_count;
}

extern "C" EXPORT int gaider_cuda_crunchiness_filter_ex(
        int64_t seed_start, int32_t n,
        int32_t climate_id,
        float threshold,
        int32_t mode_flag,
        int64_t *out_seeds, int32_t max_results) {
    if (n <= 0) return 0;
    if (max_results < 1) max_results = 1;
    SsaDevBuf d_seeds, d_count;
    SSA_CK(d_seeds.alloc((size_t)max_results * sizeof(int64_t)));
    SSA_CK(d_count.alloc(sizeof(int32_t)));
    SSA_CK(cudaMemset(d_count.p, 0, sizeof(int32_t)));
    static SsaChunker ck(1 << 22, 1 << 14, 1 << 27);
    const int bs = 512;
    for (int32_t off = 0; off < n; ) {
        int32_t cur = (int32_t)((int64_t)(n - off) < ck.chunk ? (n - off) : ck.chunk);
        ck.begin();
        k_crunchiness_filter<<<(cur + bs - 1) / bs, bs>>>(
            seed_start + off, cur, climate_id, threshold, mode_flag,
            d_seeds.as<int64_t>(), d_count.as<int32_t>(), max_results);
        SSA_CK_LAUNCH();
        ck.end();
        off += cur;
    }
    int32_t h_count = 0;
    SSA_CK(cudaMemcpy(&h_count, d_count.p, sizeof(int32_t), cudaMemcpyDeviceToHost));
    int32_t actual = h_count < max_results ? h_count : max_results;
    if (actual > 0) {
        SSA_CK(cudaMemcpy(out_seeds, d_seeds.p, (size_t)actual * sizeof(int64_t), cudaMemcpyDeviceToHost));
    }
    return h_count;
}

