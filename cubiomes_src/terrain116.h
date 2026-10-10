/* terrain116.h — the exact Java 1.16 overworld terrain (see terrain116.c). */
#ifndef TERRAIN116_H
#define TERRAIN116_H
#include <stdint.h>
#include "generator.h"
#include "biomenoise.h"

/* block codes (the blocks the 1.16 overworld terrain + carvers can place) */
enum {
    T116_AIR = 0, T116_STONE, T116_WATER, T116_LAVA, T116_BEDROCK,
    T116_GRASS, T116_DIRT, T116_COARSE_DIRT, T116_PODZOL, T116_MYCELIUM,
    T116_SAND, T116_RED_SAND, T116_GRAVEL, T116_SANDSTONE, T116_RED_SANDSTONE,
    T116_ICE, T116_PACKED_ICE, T116_SNOW_BLOCK,
    T116_TERRACOTTA, T116_WHITE_T, T116_ORANGE_T, T116_YELLOW_T, T116_BROWN_T, T116_RED_T, T116_LIGHT_GRAY_T,
    T116_CAVE_AIR, T116_MAGMA, T116_OBSIDIAN, T116_OTHER,
    T116_SPAWNER, T116_CHEST, T116_COBBLE, T116_MOSSY,
    T116_PLANKS, T116_FENCE,            /* mineshafts (oak / dark oak): solid */
    T116_RAIL, T116_COBWEB, T116_TORCH, /* mineshafts: neither air nor solid */
    T116_NBLOCKS
};
#define T116_IS_AIR(b)    ((b) == T116_AIR || (b) == T116_CAVE_AIR)
#define T116_IS_LIQUID(b) ((b) == T116_WATER || (b) == T116_LAVA)
/* Material.isSolid(): everything here but air, the liquids, rails, cobwebs, torches */
#define T116_IS_SOLID(b)  (!T116_IS_AIR(b) && !T116_IS_LIQUID(b) && (b) != T116_RAIL && (b) != T116_COBWEB && (b) != T116_TORCH)

typedef struct { int sb, top, under, underwater; float temp; int flags, ok; } T116Biome;

/* 2-D simplex / octave simplex (Java SimplexNoiseSampler) */
typedef struct { double ox, oy, oz; uint8_t p[256]; } T116Simplex;
typedef struct { T116Simplex s[4]; int n; double lac0, amp0; } T116OctSimplex;

typedef struct {
    Generator g;
    SurfaceNoise sn;
    int mc;
    uint64_t seed;
    T116OctSimplex surf;                /* surfaceDepthNoise (-3..0) */
    T116OctSimplex bl_layer, bl_cut, bl_height;   /* badlands */
    uint8_t bl_layers[64];
    T116OctSimplex fo_a, fo_b;          /* frozen ocean icebergs */
} T116;

/* one chunk being built: blocks[(y*16 + z)*16 + x] */
typedef struct {
    int cx, cz;
    int bio4[9 * 9];                    /* the 1:4 biomes, cells cx*4-2 .. cx*4+6 */
    uint8_t b[65536];
    uint8_t mask_air[8192], mask_liq[8192];
} T116Chunk;

#define T116_AT(c, x, y, z) ((c)->b[(((y) << 4) | (z)) << 4 | (x)])

int  t116_chunk_build(const T116 *t, int cx, int cz, int status, T116Chunk *c);  /* status 4 noise, 5 surface, 6 carvers, 7 liquid carvers */

int  t116_init(T116 *t, int mc, uint64_t seed);
void t116_column(const T116 *t, int x, int z, double out[33]);
void t116_column_b(const T116 *t, int x, int z, const int *bio25, double out[33]);
int  t116_chunk_noise(const T116 *t, int cx, int cz, uint8_t *out /* 65536, [(y*16+z)*16+x] */);

/* ── features (population of one chunk, LAKES .. UNDERGROUND_STRUCTURES) ──
 * The chunk and its 8 neighbours at LIQUID_CARVERS (neighbours not populated),
 * then in the chunk's own order: the water lake, the lava lake, the dungeon
 * attempts (each valid one changes the blocks and the random for the next).
 * Not modelled: mineshafts / buried treasure (structures), fossils, icebergs,
 * forest rocks, villages (lake check) and structure beards. */
typedef struct {
    int kind;                 /* 0 dungeon, 1 lava lake, 2 water lake */
    int x, y, z;              /* dungeon: spawner; lake: decorator x, (dropped) y, z */
    int sx, sz;               /* dungeon half sizes */
    int ok;                   /* generated */
    int attempt;              /* dungeon attempt 0..7 */
    int ox, oy, oz;           /* lake box origin (after the drop) */
} T116Feat;

typedef struct T116Cache T116Cache;
T116Cache *t116_cache_new(int cap);
void t116_cache_free(T116Cache *c);
/* a chunk of the cache at status st (4 noise, 5 surface, 6 carvers, 7 liquid carvers) */
const T116Chunk *t116_cache_chunk(const T116 *t, T116Cache *c, int cx, int cz, int st);
int t116_top_solid(const T116Chunk *c, int x, int z);
/* the population of chunk (cx, cz): returns the number of features in out */
int t116_populate(const T116 *t, T116Cache *cache, int cx, int cz, T116Feat *out, int cap,
                  T116Chunk *result /* optional: the chunk after it */);
/* 0: chunk (cx, cz) certainly has no dungeon (superset test, no terrain built) */
int t116_dungeons_maybe(const T116 *t, int cx, int cz);
/* getBiomeAt(g, 4, x, 0, z), memoised per thread (same answer) */
int t116_biome4(const Generator *g, int x, int z);
/* want: 1 lakes only (cheaper), 3 lakes + mineshafts + dungeons */
int t116_populate_ex(const T116 *t, T116Cache *cache, int cx, int cz, T116Feat *out, int cap,
                     T116Chunk *result, int want);
#endif
