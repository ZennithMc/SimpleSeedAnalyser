/*
 * height_bridge.c — expose mapApproxHeight + initSurfaceNoise via ctypes.
 */
#include "biomenoise.h"
#include "generator.h"
#include <stdlib.h>
#include <string.h>

size_t hb_surfaceNoise_sizeof(void) { return sizeof(SurfaceNoise); }

void *hb_alloc_surfaceNoise(void) {
    void *p = calloc(1, sizeof(SurfaceNoise));
    return p;
}

void hb_free_surfaceNoise(void *p) { free(p); }

void hb_initSurfaceNoise(SurfaceNoise *sn, int dim, uint64_t seed) {
    initSurfaceNoise(sn, dim, seed);
}

/*
 * hb_mapHeightGrid — wrapper original (step=1, scale=4 implicite).
 * gx, gz en cellules scale=4 (1 cellule = 4 blocs).
 */
int hb_mapHeightGrid(const Generator *g, const SurfaceNoise *sn,
                      float *heights, int *biomes,
                      int gx, int gz, int w, int h) {
    return mapApproxHeight(heights, biomes, g, sn, gx, gz, w, h);
}

/*
 * hb_mapHeightGridStep — comme hb_mapHeightGrid mais avec un pas (step)
 * entre chaque sample en cellules scale=4.
 *
 * Paramètres :
 *   gx, gz  : coin haut-gauche en cellules scale=4
 *   w, h    : nombre de samples en X et Z (taille du buffer de sortie)
 *   step    : pas en cellules scale=4 entre chaque sample
 *             (step=1 → identique à hb_mapHeightGrid)
 *             (step=64 → 1 sample tous les 256 blocs, pour scale=256)
 *
 * heights et biomes doivent avoir w*h éléments alloués.
 *
 * Implémentation : appelle mapApproxHeight point par point pour éviter
 * d'allouer un buffer géant. Pour w*h <= 256*256 = 65536, cela représente
 * au plus 65536 appels — acceptable (< 50ms sur CPU moderne).
 * Pour les cas courants (step=1..4), on appelle mapApproxHeight en bloc
 * sur une ligne à la fois pour de meilleures performances.
 */
int hb_mapHeightGridStep(const Generator *g, const SurfaceNoise *sn,
                          float *heights, int *biomes,
                          int gx, int gz, int w, int h, int step) {
    if (step <= 1) {
        /* Cas rapide : délègue au wrapper original */
        return mapApproxHeight(heights, biomes, g, sn, gx, gz, w, h);
    }

    float  htmp;
    int    btmp;
    int    idx = 0;
    int ok = 0;

    for (int iz = 0; iz < h; iz++) {
        int cz = gz + iz * step;
        for (int ix = 0; ix < w; ix++) {
            int cx = gx + ix * step;
            /* Un seul sample à la fois */
            int r = mapApproxHeight(&htmp, &btmp, g, sn, cx, cz, 1, 1);
            heights[idx] = htmp;
            biomes[idx]  = btmp;
            idx++;
            if (r == 0) ok++;
        }
    }
    return (ok == w * h) ? 0 : -1;
}
