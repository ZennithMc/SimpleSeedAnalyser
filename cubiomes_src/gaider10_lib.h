#pragma once

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

#if _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT
#endif

typedef struct {
    uint64_t structure_seed;
    int16_t start_chunk_x;
    int16_t portal_chunk_x;
    int16_t start_chunk_z;
    int16_t portal_chunk_z;
} Layout;

EXPORT uint32_t generate_layouts(uint64_t structure_seed_start, uint64_t structure_seed_end, int superflat, Layout *out, uint32_t out_len);
EXPORT int test_world_seed(uint64_t world_seed, int32_t start_chunk_x, int32_t start_chunk_z);

#ifdef __cplusplus
}
#endif