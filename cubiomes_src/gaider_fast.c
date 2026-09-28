// gaider_fast.c — Native C implementation of Gaider's stronghold check.
// Bit-perfect equivalent of stronghold_eyes.py functions:
//   - Xrsr128 (XoroShiRo128++) RNG
//   - _xrsr_get_decoration_seed
//   - _skip_portal_room (XOR matrix)
//   - count_eyes per Gaider matrix
//
// Used by SSA's search worker for high-eye-count scans —
// ~100x faster than the pure-Python equivalent.

#include <stdint.h>
#include <stddef.h>

#ifdef _WIN32
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT __attribute__((visibility("default")))
#endif

#define XR_SILVER 0x6A09E667F3BCC909ULL
#define XR_GOLDEN 0x9E3779B97F4A7C15ULL
#define XR_MIX1   0xBF58476D1CE4E5B9ULL
#define XR_MIX2   0x94D049BB133111EBULL

// Auto-generated XOR matrices (64 × 2 × uint64_t each)
static const uint64_t XOR_LO[64][2] = {
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

static const uint64_t XOR_HI[64][2] = {
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


// ─────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────

static inline uint64_t gaider_rotl64(uint64_t x, int k) {
    return (x << k) | (x >> (64 - k));
}

// _xr_mix64
static inline uint64_t xr_mix64(uint64_t z) {
    z = (z ^ (z >> 30)) * XR_MIX1;
    z = (z ^ (z >> 27)) * XR_MIX2;
    return z ^ (z >> 31);
}

typedef struct {
    uint64_t lo;
    uint64_t hi;
} XrsrState;

// Xrsr128.set_seed: lo = mix64(seed^SILVER), hi = mix64((seed^SILVER)+GOLDEN)
static inline void xrsr_set_seed(XrsrState *s, uint64_t seed) {
    uint64_t v = seed ^ XR_SILVER;
    s->lo = xr_mix64(v);
    s->hi = xr_mix64(v + XR_GOLDEN);
    if ((s->lo | s->hi) == 0) {
        s->lo = 0xFFFFFFFFFFFFFFFFULL - 7046029254386353131ULL + 1ULL; // -7046029254386353131 in u64
        // Equivalent to (1<<64) - 7046029254386353131 = 0x9E6D62D0E69CFEED
        s->lo = 0x9E6D62D0E69CFEEDULL;
        s->hi = XR_SILVER;
    }
}

// Internal _xrsr_long: returns (rotl(lo+hi, 17) + lo), then advances state.
static inline uint64_t xrsr_long_internal(XrsrState *s) {
    uint64_t lo = s->lo, hi = s->hi;
    uint64_t result = gaider_rotl64(lo + hi, 17) + lo;
    uint64_t hi_xor = hi ^ lo;
    s->lo = gaider_rotl64(lo, 49) ^ hi_xor ^ (hi_xor << 21);
    s->hi = gaider_rotl64(hi_xor, 28);
    return result;
}

// next(bits): returns top `bits` of xrsr_long, sign-extended for bits=32.
// For 24 bits: (uint32_t)(r >> 40)
// For 32 bits: (int32_t)(r >> 32)  -- sign-extended into uint64 in callers
static inline uint64_t xrsr_next_bits(XrsrState *s, int bits) {
    uint64_t r = xrsr_long_internal(s);
    return r >> (64 - bits);
}

// next_long(): ((int64_t)((int32_t)next(32)) << 32) + (int64_t)((int32_t)next(32))
// Both halves are sign-extended from 32-bit ints (Java cast semantics).
static inline uint64_t xrsr_next_long(XrsrState *s) {
    int32_t hi32 = (int32_t)(xrsr_long_internal(s) >> 32);
    int32_t lo32 = (int32_t)(xrsr_long_internal(s) >> 32);
    int64_t result = ((int64_t)hi32 << 32) + (int64_t)lo32;
    return (uint64_t)result;
}

// next_float(): next(24) / 2^24
static inline double xrsr_next_float(XrsrState *s) {
    uint32_t bits = (uint32_t)(xrsr_long_internal(s) >> 40);
    return ((double)bits) * (1.0 / 16777216.0);
}

// ─────────────────────────────────────────────────────────────────────
// _xrsr_get_decoration_seed: same logic as Python
// ─────────────────────────────────────────────────────────────────────
static inline uint64_t decoration_seed(uint64_t world_seed,
                                         int32_t chunk_x, int32_t chunk_z) {
    XrsrState s;
    xrsr_set_seed(&s, world_seed);
    uint64_t a = xrsr_next_long(&s) | 1ULL;
    uint64_t b = xrsr_next_long(&s) | 1ULL;
    int64_t bx = (int64_t)chunk_x * 16;
    int64_t bz = (int64_t)chunk_z * 16;
    return ((uint64_t)bx * a + (uint64_t)bz * b) ^ world_seed;
}

// ─────────────────────────────────────────────────────────────────────
// _skip_portal_room: XOR matrix advance
// ─────────────────────────────────────────────────────────────────────
static inline void skip_portal_room(XrsrState *s) {
    uint64_t lo = s->lo, hi = s->hi;
    uint64_t new_lo = 0, new_hi = 0;
    for (int i = 0; i < 64; i++) {
        if ((lo >> i) & 1ULL) {
            new_hi ^= XOR_LO[i][0];
            new_lo ^= XOR_LO[i][1];
        }
    }
    for (int i = 0; i < 64; i++) {
        if ((hi >> i) & 1ULL) {
            new_hi ^= XOR_HI[i][0];
            new_lo ^= XOR_HI[i][1];
        }
    }
    s->lo = new_lo;
    s->hi = new_hi;
}

// ─────────────────────────────────────────────────────────────────────
// Eye-RNG offset (Xoroshiro decoration seed), 1.18+. Mirrors Python
// stronghold_eyes._eye_feature_offset:
//   setFeatureSeed(deco, index, step) = deco + index + 10000*step.
//   1.18.0/1.18.1 (mc<=22): stronghold step STRONGHOLDS(5), index 0  -> +50000.
//   1.19+ (mc>=24): step SURFACE_STRUCTURES(4), index 19 (default, UNVERIFIED)
//                   -> +40019.
// ─────────────────────────────────────────────────────────────────────
static inline uint64_t eye_offset_xrsr(int mc_version) {
    if (mc_version <= 22) return 50000ULL;   // 1.18.0/1.18.1: STRONGHOLDS step
    if (mc_version == 23) return 40000ULL + 8ULL;  // 1.19.0-1.19.2: registration order
    return 40000ULL + 19ULL;                 // 1.19.3+: SURFACE_STRUCTURES, alphabetical (idx 19)
}

static inline int count_eyes_at_chunk_off(uint64_t world_seed, int32_t cx,
                                            int32_t cz, uint64_t off) {
    XrsrState s;
    uint64_t deco = decoration_seed(world_seed, cx, cz);
    xrsr_set_seed(&s, deco + off);
    skip_portal_room(&s);
    int eyes = 0;
    for (int i = 0; i < 12; i++) if (xrsr_next_float(&s) > 0.9) eyes++;
    return eyes;
}

static inline int check_12eye_off(uint64_t world_seed, int32_t cx,
                                    int32_t cz, uint64_t off) {
    XrsrState s;
    uint64_t deco = decoration_seed(world_seed, cx, cz);
    xrsr_set_seed(&s, deco + off);
    skip_portal_room(&s);
    for (int i = 0; i < 12; i++) if (xrsr_next_float(&s) <= 0.9) return 0;
    return 1;
}

// ─────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────

// NOTE: these two 3-arg exports keep the legacy 1.19+ offset (+40019). The live
// search paths go through the *_with_init batch functions below, which route the
// offset by mc_version (so 1.18.1 correctly uses +50000).
EXPORT int gaider_count_eyes_at_chunk(uint64_t world_seed,
                                        int32_t chunk_x, int32_t chunk_z) {
    return count_eyes_at_chunk_off(world_seed, chunk_x, chunk_z, 40019ULL);
}

EXPORT int gaider_check_12eye_fast(uint64_t world_seed,
                                     int32_t chunk_x, int32_t chunk_z) {
    return check_12eye_off(world_seed, chunk_x, chunk_z, 40019ULL);
}

// Batch scan: tests N seeds at given chunks.
// Returns number of matches; out_idx[0..matches-1] contains source indices,
// out_eyes[0..matches-1] contains eye counts (if non-NULL).
EXPORT int gaider_batch_scan(const uint64_t *seeds, int32_t n,
                              const int32_t *chunk_xs,
                              const int32_t *chunk_zs,
                              int min_eyes,
                              int32_t *out_idx,
                              int32_t *out_eyes) {
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int32_t cx = chunk_xs ? chunk_xs[i] : 0;
        int32_t cz = chunk_zs ? chunk_zs[i] : 0;
        int e = gaider_count_eyes_at_chunk(seeds[i], cx, cz);
        if (e >= min_eyes) {
            out_idx[matches] = i;
            if (out_eyes) out_eyes[matches] = e;
            matches++;
        }
    }
    return matches;
}

// ─────────────────────────────────────────────────────────────────────
// gaider_batch_12eye_with_init: scans N consecutive seeds starting from
// seed_start, calling initFirstStronghold internally to get the chunk for
// each, then doing the 12-eye Gaider check. This is the fastest path
// since the entire hot loop runs in C.
// ─────────────────────────────────────────────────────────────────────

#include "finders.h"

// Scans [seed_start, seed_start + n) consecutively. Writes matching seeds
// to out_seeds and their eye counts to out_eyes (both must hold n slots).
// Returns number of matches.
EXPORT int gaider_batch_12eye_with_init(int64_t seed_start, int32_t n,
                                          int mc_version,
                                          int64_t *out_seeds,
                                          int32_t *out_eyes) {
    StrongholdIter it;
    uint64_t off = eye_offset_xrsr(mc_version);
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        Pos pos = initFirstStronghold(&it, mc_version, (uint64_t)seed);
        int32_t cx = pos.x >> 4;
        int32_t cz = pos.z >> 4;
        if (check_12eye_off((uint64_t)seed, cx, cz, off)) {
            out_seeds[matches] = seed;
            if (out_eyes) out_eyes[matches] = 12;
            matches++;
        }
    }
    return matches;
}

// Batch initFirstStronghold: computes the chunk (cx, cz) for each seed.
// Pre-computation step before GPU scan — avoids the Python overhead of
// calling initFirstStronghold one seed at a time via ctypes.
//
// Throughput: ~7M seeds/s on CPU (vs ~750k/s when called from Python).
EXPORT void gaider_batch_compute_chunks(int64_t seed_start, int32_t n,
                                          int mc_version,
                                          int32_t *out_cx, int32_t *out_cz) {
    StrongholdIter it;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        Pos pos = initFirstStronghold(&it, mc_version, (uint64_t)seed);
        out_cx[i] = pos.x >> 4;
        out_cz[i] = pos.z >> 4;
    }
}
EXPORT int gaider_batch_strict_with_init(int64_t seed_start, int32_t n,
                                            int mc_version,
                                            int min_eyes,
                                            int64_t *out_seeds,
                                            int32_t *out_eyes) {
    StrongholdIter it;
    uint64_t off = eye_offset_xrsr(mc_version);
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        Pos pos = initFirstStronghold(&it, mc_version, (uint64_t)seed);
        int32_t cx = pos.x >> 4;
        int32_t cz = pos.z >> 4;
        int e = count_eyes_at_chunk_off((uint64_t)seed, cx, cz, off);
        if (e >= min_eyes) {
            out_seeds[matches] = seed;
            if (out_eyes) out_eyes[matches] = e;
            matches++;
        }
    }
    return matches;
}

// 9-chunk prefilter (more permissive, higher recall).
EXPORT int gaider_batch_prefilter_with_init(int64_t seed_start, int32_t n,
                                              int mc_version,
                                              int min_eyes,
                                              int64_t *out_seeds,
                                              int32_t *out_eyes) {
    StrongholdIter it;
    uint64_t off = eye_offset_xrsr(mc_version);
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        Pos pos = initFirstStronghold(&it, mc_version, (uint64_t)seed);
        int32_t cx0 = pos.x >> 4;
        int32_t cz0 = pos.z >> 4;
        int best = 0;
        for (int dcx = -1; dcx <= 1 && best < min_eyes; dcx++) {
            for (int dcz = -1; dcz <= 1 && best < min_eyes; dcz++) {
                int e = count_eyes_at_chunk_off((uint64_t)seed,
                                                  cx0 + dcx, cz0 + dcz, off);
                if (e > best) best = e;
            }
        }
        if (best >= min_eyes) {
            out_seeds[matches] = seed;
            if (out_eyes) out_eyes[matches] = best;
            matches++;
        }
    }
    return matches;
}

// ═════════════════════════════════════════════════════════════════════
// MC 1.13-1.17 — pipeline LCG (java.util.Random) pour le comptage d'yeux.
// Copie bit-perfect du pipeline Python validé (et du kernel CUDA) :
//   deco = setDecorationSeed_LCG(seed, cx*16, cz*16)   [+ entre les termes]
//   JavaRandom(deco + 50000) ; saut O(1) du préfixe PortalRoom (760 nextFloat)
//   ; 12 nextFloat > 0.9.
// ═════════════════════════════════════════════════════════════════════
#define LCG_MULT   0x5DEECE66DULL
#define LCG_ADD    0xBULL
#define LCG_MASK   ((1ULL << 48) - 1)
// Saut fermé de 760 pas LCG : seed = (seed*M + A) & MASK  (validé == 760 itér.)
#define LCG_SKIP_M 0xe5cfdccc10e1ULL
#define LCG_SKIP_A 0x2fc9e05b45b8ULL

static inline void java_set_seed_l(uint64_t *seed, uint64_t value) {
    *seed = (value ^ LCG_MULT) & LCG_MASK;
}
static inline int32_t java_next_l(uint64_t *seed, int bits) {
    *seed = (*seed * LCG_MULT + LCG_ADD) & LCG_MASK;
    return (int32_t)((int64_t)*seed >> (48 - bits));
}
static inline int64_t java_next_long_l(uint64_t *seed) {
    int32_t hi = java_next_l(seed, 32);
    int32_t lo = java_next_l(seed, 32);
    return (int64_t)(((uint64_t)(int64_t)hi << 32) + (uint64_t)(int64_t)lo);
}
static inline uint64_t decoration_seed_lcg(uint64_t world_seed,
                                           int32_t chunk_x, int32_t chunk_z) {
    uint64_t s;
    java_set_seed_l(&s, world_seed);
    uint64_t a = (uint64_t)java_next_long_l(&s) | 1ULL;
    uint64_t b = (uint64_t)java_next_long_l(&s) | 1ULL;
    uint64_t bx = (uint64_t)((int64_t)chunk_x * 16);
    uint64_t bz = (uint64_t)((int64_t)chunk_z * 16);
    return (bx * a + bz * b) ^ world_seed;
}

EXPORT int gaider_count_eyes_at_chunk_lcg(uint64_t world_seed,
                                          int32_t chunk_x, int32_t chunk_z) {
    uint64_t deco = decoration_seed_lcg(world_seed, chunk_x, chunk_z);
    uint64_t s;
    java_set_seed_l(&s, deco + 50000ULL);
    s = (s * LCG_SKIP_M + LCG_SKIP_A) & LCG_MASK;   // saut O(1) du préfixe
    int eyes = 0;
    for (int i = 0; i < 12; i++) {
        uint32_t bits = (uint32_t)java_next_l(&s, 24);
        if (bits >= 15099495u) eyes++;            // > 0.9 * 2^24
    }
    return eyes;
}

// ─────────────────────────────────────────────────────────────────────
// Seed-ARRAY eye prefilter (seedlist eye finder, all versions). For each seed,
// the MAX single-chunk eye count over a (2*grid+1)^2 chunk window around its
// snapped start chunk (cxs[i],czs[i]); keep those whose max >= min_eyes.
//   - This is EXACT for the 12-eye case under the verified in-game rule: a
//     portal lights up as 12/12 if ANY chunk it touches yields 12 on its own
//     (the window covers those chunks). The Python side re-validates survivors
//     exactly (_eyes_per_frame, which applies the same rule).
//   - Version-correct: LCG (<1.18) vs Xoroshiro (1.18+, version offset).
// cxs/czs come from seedlist_first_stronghold (cubiomes-snapped positions).
// ─────────────────────────────────────────────────────────────────────
EXPORT int seedlist_eyes_prefilter_arr(const int64_t *seeds, const int32_t *cxs,
        const int32_t *czs, int32_t n, int mc, int min_eyes, int grid,
        int64_t *out_seeds, int32_t *out_eyes) {
    int is_lcg = (mc < 22);
    uint64_t off = is_lcg ? 0ULL : eye_offset_xrsr(mc);
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int cx0 = cxs[i], cz0 = czs[i];
        int best = 0;
        for (int dx = -grid; dx <= grid && best < 12; dx++) {
            for (int dz = -grid; dz <= grid && best < 12; dz++) {
                int e = is_lcg
                    ? gaider_count_eyes_at_chunk_lcg((uint64_t)seeds[i], cx0 + dx, cz0 + dz)
                    : count_eyes_at_chunk_off((uint64_t)seeds[i], cx0 + dx, cz0 + dz, off);
                if (e > best) best = e;
            }
        }
        if (best >= min_eyes) {
            out_seeds[matches] = seeds[i];
            out_eyes[matches] = best;
            matches++;
        }
    }
    return matches;
}

EXPORT int gaider_batch_12eye_with_init_lcg(int64_t seed_start, int32_t n,
                                            int mc_version,
                                            int64_t *out_seeds,
                                            int32_t *out_eyes) {
    StrongholdIter it;
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        Pos pos = initFirstStronghold(&it, mc_version, (uint64_t)seed);
        int32_t cx = pos.x >> 4, cz = pos.z >> 4;
        if (gaider_count_eyes_at_chunk_lcg((uint64_t)seed, cx, cz) >= 12) {
            out_seeds[matches] = seed;
            if (out_eyes) out_eyes[matches] = 12;
            matches++;
        }
    }
    return matches;
}

EXPORT int gaider_batch_strict_with_init_lcg(int64_t seed_start, int32_t n,
                                             int mc_version, int min_eyes,
                                             int64_t *out_seeds,
                                             int32_t *out_eyes) {
    StrongholdIter it;
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        Pos pos = initFirstStronghold(&it, mc_version, (uint64_t)seed);
        int32_t cx = pos.x >> 4, cz = pos.z >> 4;
        int e = gaider_count_eyes_at_chunk_lcg((uint64_t)seed, cx, cz);
        if (e >= min_eyes) {
            out_seeds[matches] = seed;
            if (out_eyes) out_eyes[matches] = e;
            matches++;
        }
    }
    return matches;
}

EXPORT int gaider_batch_prefilter_with_init_lcg(int64_t seed_start, int32_t n,
                                                int mc_version, int min_eyes,
                                                int64_t *out_seeds,
                                                int32_t *out_eyes) {
    StrongholdIter it;
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        Pos pos = initFirstStronghold(&it, mc_version, (uint64_t)seed);
        int32_t cx0 = pos.x >> 4, cz0 = pos.z >> 4;
        int best = 0;
        for (int dcx = -1; dcx <= 1 && best < min_eyes; dcx++) {
            for (int dcz = -1; dcz <= 1 && best < min_eyes; dcz++) {
                int e = gaider_count_eyes_at_chunk_lcg((uint64_t)seed,
                                                       cx0 + dcx, cz0 + dcz);
                if (e > best) best = e;
            }
        }
        if (best >= min_eyes) {
            out_seeds[matches] = seed;
            if (out_eyes) out_eyes[matches] = best;
            matches++;
        }
    }
    return matches;
}

// Grille ±radius autour de la position initFirstStronghold (le portail est
// decale de la BFS, jamais AU chunk de depart → un seul chunk = ~0% recall).
// Comme initFirstStronghold donne la VRAIE position (snap biome), le portail
// est dans ±7 chunks → recall eleve. Mirroir LCG de la logique GPU full_scan.
EXPORT int gaider_batch_grid_with_init_lcg(int64_t seed_start, int32_t n,
                                           int mc_version, int min_eyes,
                                           int radius,
                                           int64_t *out_seeds,
                                           int32_t *out_eyes) {
    StrongholdIter it;
    int matches = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        Pos pos = initFirstStronghold(&it, mc_version, (uint64_t)seed);
        int32_t cx0 = pos.x >> 4, cz0 = pos.z >> 4;
        int best = 0;
        for (int dcx = -radius; dcx <= radius && best < min_eyes; dcx++) {
            for (int dcz = -radius; dcz <= radius && best < min_eyes; dcz++) {
                int e = gaider_count_eyes_at_chunk_lcg((uint64_t)seed,
                                                       cx0 + dcx, cz0 + dcz);
                if (e > best) best = e;
            }
        }
        if (best >= min_eyes) {
            out_seeds[matches] = seed;
            if (out_eyes) out_eyes[matches] = best;
            matches++;
        }
    }
    return matches;
}
