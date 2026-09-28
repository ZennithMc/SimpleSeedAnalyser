// gtypes.h — clash-free shared types (no biome-id constants), used by both the
// cubiomes-side extractor and the self-contained .cuh.
#ifndef GTYPES_H
#define GTYPES_H
#include <stdint.h>
typedef struct {
    uint8_t d[257]; uint8_t h2;
    double a,b,c, amplitude, lacunarity, d2, t2;
} PerlinNoiseG;
typedef struct { int type; int mc; int zoom, edge; uint64_t layerSalt, startSalt, startSeed; int p, p2; } LayerG;
enum {
    G_CONTINENT=1, G_ZOOM_FUZZY, G_ZOOM, G_LAND, G_ISLAND, G_SNOW, G_COOL,
    G_HEAT, G_SPECIAL, G_MUSHROOM, G_DEEPOCEAN, G_BIOME, G_NOISE, G_BAMBOO,
    G_BIOMEEDGE, G_HILLS, G_RIVER, G_SMOOTH, G_SUNFLOWER, G_SHORE,
    G_RIVERMIX, G_OCEANTEMP, G_OCEANMIX
};
#define GL_MAX 64
#endif
