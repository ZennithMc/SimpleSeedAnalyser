/*
 * noise_bridge.c — fonctions helper pour exposer les climate noise maps
 * via ctypes depuis Python, sans avoir besoin de mapper la struct BiomeNoise.
 *
 * Stratégie : on alloue BiomeNoise dynamiquement (sizeof connu côté C)
 * et on expose des fonctions qui opèrent sur un pointeur opaque void*.
 */
#include "biomenoise.h"
#include "generator.h"
#include <stdlib.h>
#include <string.h>

/* Retourne sizeof(BiomeNoise) pour que Python puisse allouer le bon buffer. */
size_t biomeNoise_sizeof(void) { return sizeof(BiomeNoise); }

/* Initialise et seed un BiomeNoise pour un seul paramètre clima (nptype). */
void nb_setClimateParaSeed(BiomeNoise *bn, uint64_t seed, int large, int nptype, int nmax)
{
    setClimateParaSeed(bn, seed, large, nptype, nmax);
}

/*
 * Remplit out[W*H] avec les valeurs de sampleClimatePara pour une grille
 * gx..gx+W x gz..gz+H en coordonnées BLOC / scale.
 * Chaque cellule = sampleClimatePara(bn, NULL, x_bloc/4.0, z_bloc/4.0)
 * (cubiomes Viewer fait x/4, z/4 pour afficher à scale=4).
 */
void nb_sampleClimateGrid(const BiomeNoise *bn, double *out,
                           int gx, int gz, int W, int H, int scale)
{
    double inv = 0.25 * (double)scale;  /* x_noise = gx*scale * 0.25 = gx*scale/4 */
    for (int zi = 0; zi < H; zi++) {
        for (int xi = 0; xi < W; xi++) {
            double x = (gx + xi) * inv;
            double z = (gz + zi) * inv;
            out[zi * W + xi] = sampleClimatePara(bn, NULL, x, z);
        }
    }
}

/*
 * Comme nb_sampleClimateGrid, mais ne SOMME que les octaves jusqu'à
 * (octave_pair, stream) inclus. Ordre des octaves : 0A,0B,1A,1B,2A,2B,...
 *   "jusqu'à 2B" (include_b=1) = octA[0..2] + octB[0..2]
 *   "jusqu'à 2A" (include_b=0) = octA[0..2] + octB[0..1]
 * On limite temporairement octcnt (sampleOctave boucle sur octcnt), puis on
 * restaure — donc bit-exact avec le rendu complet quand octave_pair = dernier.
 * nptype = le même climat passé à setClimateParaSeed (T=0,H=1,C=2,E=3,W=5).
 */
/* Somme un OctaveNoise sur les 'maxcnt' PREMIÈRES octaves — copie EXACTE de
 * sampleOctave() (noise.c) mais bornée, et surtout SANS muter la struct.
 * Read-only => thread-safe (plusieurs workers noise partagent le même bn). */
static double nb_sampleOctaveN(const OctaveNoise *o, double x, double y, double z,
                               int maxcnt)
{
    double v = 0;
    int n = (maxcnt < o->octcnt) ? maxcnt : o->octcnt;
    if (n < 0) n = 0;
    for (int i = 0; i < n; i++) {
        const PerlinNoise *p = o->octaves + i;
        double lf = p->lacunarity;
        double ax = maintainPrecision(x * lf);
        double ay = maintainPrecision(y * lf);
        double az = maintainPrecision(z * lf);
        v += p->amplitude * samplePerlin(p, ax, ay, az, 0, 0);
    }
    return v;
}

/* sampleDoublePerlin() borné aux octaves (maxA, maxB) — read-only. */
static double nb_sampleDoublePerlinN(const DoublePerlinNoise *dp,
                                     double x, double y, double z,
                                     int maxA, int maxB)
{
    const double f = 337.0 / 331.0;
    double v = nb_sampleOctaveN(&dp->octA, x,   y,   z,   maxA)
             + nb_sampleOctaveN(&dp->octB, x*f, y*f, z*f, maxB);
    return v * dp->amplitude;
}

/*
 * Rend le noise SOMMÉ jusqu'à (octave_pair, stream) inclus, en LECTURE SEULE
 * (aucune mutation d'octcnt -> thread-safe, pas de blocage entre workers).
 * Ordre 0A,0B,1A,1B,… : "jusqu'à 2B" = octA[0..2]+octB[0..2] ;
 * "jusqu'à 2A" = octA[0..2]+octB[0..1]. Bit-exact avec le noise complet quand
 * octave_pair = dernière paire. nptype = T=0,H=1,C=2,E=3,W=5 (non-DEPTH).
 */
void nb_sampleClimateGridOctaves(BiomeNoise *bn, double *out,
    int gx, int gz, int W, int H, int scale,
    int nptype, int octave_pair, int include_b)
{
    const DoublePerlinNoise *dp = &bn->climate[nptype];
    int maxA = octave_pair + 1;
    int maxB = include_b ? (octave_pair + 1) : octave_pair;
    double inv = 0.25 * (double)scale;
    for (int zi = 0; zi < H; zi++) {
        for (int xi = 0; xi < W; xi++) {
            double x = (gx + xi) * inv;
            double z = (gz + zi) * inv;
            out[zi * W + xi] = nb_sampleDoublePerlinN(dp, x, 0.0, z, maxA, maxB);
        }
    }
}

/*
 * Remplit ampA[]/ampB[] avec l'amplitude de chaque octave du DoublePerlin du
 * climat nptype, et *nA/*nB avec le nombre d'octaves (A et B). cap = taille des
 * buffers. Sert à calculer le % de génération cumulé côté Python :
 *   % (jusqu'à pair,stream) = (Σ ampA[0..pair] + Σ ampB[0..pair-ou-pair-1]) / total.
 */
void nb_octave_info(BiomeNoise *bn, int nptype, double *ampA, double *ampB,
    int *nA, int *nB, int cap)
{
    DoublePerlinNoise *dp = &bn->climate[nptype];
    int ca = dp->octA.octcnt, cb = dp->octB.octcnt;
    if (ca > cap) ca = cap;
    if (cb > cap) cb = cap;
    for (int i = 0; i < ca; i++) ampA[i] = dp->octA.octaves[i].amplitude;
    for (int i = 0; i < cb; i++) ampB[i] = dp->octB.octaves[i].amplitude;
    *nA = dp->octA.octcnt;
    *nB = dp->octB.octcnt;
}

/* ------------------------------------------------------------------------
 * RAW PERLIN LATTICE GRADIENT VECTORS ("gradevec") — the true 3D vectors.
 *
 * Every Perlin octave assigns, to each INTEGER lattice node (i1,i2,i3), one
 * of 16 fixed gradient edge-vectors, selected by the permutation table:
 *     A  = d[i1] + i2 ;  B = d[A] + i3 ;  gi = d[B] & 15
 * (uint8 arithmetic — matches samplePerlin's lower corner exactly). The
 * vector is (cEdgeX[gi], cEdgeY[gi], cEdgeZ[gi]). Because the hash chains
 * through i2, the chosen vector CHANGES with the Y lattice layer — so a
 * Y-slice is meaningful here (unlike the 2D climate value field).
 *
 * We map the requested world AABB into this octave's lattice space
 *     lat = world * (0.25*scale) * ffac * lacunarity  + offset(a/b/c)
 * (ffac = 337/331 for the B stream, matching nb_sampleDoublePerlinN), walk
 * every integer node in view, and emit its world position + gradient vector.
 * Returns the node count written (bounded by cap).
 * ---------------------------------------------------------------------- */
static const double cEdgeX[16] = { 1.0,-1.0, 1.0,-1.0, 1.0,-1.0, 1.0,-1.0,
                                   0.0, 0.0, 0.0, 0.0, 1.0, 0.0,-1.0, 0.0 };
static const double cEdgeY[16] = { 1.0, 1.0,-1.0,-1.0, 0.0, 0.0, 0.0, 0.0,
                                   1.0,-1.0, 1.0,-1.0, 1.0,-1.0, 1.0,-1.0 };
static const double cEdgeZ[16] = { 0.0, 0.0, 0.0, 0.0, 1.0, 1.0,-1.0,-1.0,
                                   1.0, 1.0,-1.0,-1.0, 0.0, 1.0, 0.0,-1.0 };

int nb_octave_perlin_vectors(BiomeNoise *bn, int nptype,
    int octave_pair, int stream,
    double wx0, double wz0, double wx1, double wz1, double y, int scale,
    double *out_wx, double *out_wz,
    double *out_gx, double *out_gy, double *out_gz, int cap)
{
    if (cap <= 0) return 0;
    DoublePerlinNoise *dp = &bn->climate[nptype];
    const OctaveNoise *oct = stream ? &dp->octB : &dp->octA;
    if (octave_pair < 0 || octave_pair >= oct->octcnt) return 0;
    const PerlinNoise *p = &oct->octaves[octave_pair];
    const uint8_t *idx = p->d;

    double ffac = stream ? (337.0 / 331.0) : 1.0;
    double inv  = 0.25 * (double)scale;
    double sk   = inv * ffac * p->lacunarity;      /* world -> lattice scale */
    if (sk == 0.0) return 0;

    /* Constant Y lattice layer for this slice. */
    double ay = y * sk + p->b;
    int i2 = (int)floor(ay);
    uint8_t h2 = (uint8_t)i2;

    /* Lattice-node integer bounds covering the world AABB. */
    double dx0 = wx0 * sk + p->a, dx1 = wx1 * sk + p->a;
    double dz0 = wz0 * sk + p->c, dz1 = wz1 * sk + p->c;
    if (dx1 < dx0) { double t = dx0; dx0 = dx1; dx1 = t; }
    if (dz1 < dz0) { double t = dz0; dz0 = dz1; dz1 = t; }
    int i1lo = (int)floor(dx0), i1hi = (int)floor(dx1) + 1;
    int i3lo = (int)floor(dz0), i3hi = (int)floor(dz1) + 1;

    /* Uniform sub-sampling so the WHOLE viewport is covered within `cap`
     * (a plain early-return filled only the top rows on fine octaves). When
     * the lattice has more nodes than cap, keep every `stride`-th node on
     * both axes so arrows stay evenly spread and readable. */
    long long nx = (long long)i1hi - (long long)i1lo + 1;
    long long nz = (long long)i3hi - (long long)i3lo + 1;
    if (nx <= 0 || nz <= 0) return 0;
    long long total = nx * nz;
    int stride = 1;
    if (total > (long long)cap && cap > 0)
    {
        double s = ceil(sqrt((double)total / (double)cap));
        stride = (s < 1.0) ? 1 : (int)s;
    }

    int n = 0;
    for (int i3 = i3lo; i3 <= i3hi; i3 += stride)
    {
        uint8_t h3 = (uint8_t)i3;
        double wz = ((double)i3 - p->c) / sk;
        for (int i1 = i1lo; i1 <= i1hi; i1 += stride)
        {
            if (n >= cap) return n;
            uint8_t h1 = (uint8_t)i1;
            uint8_t A  = (uint8_t)(idx[h1] + h2);
            uint8_t B  = (uint8_t)(idx[A]  + h3);
            uint8_t gi = (uint8_t)(idx[B] & 0x0f);
            out_wx[n] = ((double)i1 - p->a) / sk;
            out_wz[n] = wz;
            out_gx[n] = cEdgeX[gi];
            out_gy[n] = cEdgeY[gi];
            out_gz[n] = cEdgeZ[gi];
            n++;
        }
    }
    return n;
}
