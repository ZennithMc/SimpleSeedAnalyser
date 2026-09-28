/*
 * drawing_bridge.c — Moteur de recherche de seeds par ressemblance de dessin.
 *
 * Approche (identique à seedmap.app) : terre / océan / mushroom se déterminent
 * UNIQUEMENT par la continentalness (NP_CONTINENTALNESS), avec des seuils fixes
 * issus de la table de biomes de cubiomes (finders.c) :
 *
 *      continentalness * 10000  <  -10500   → MUSHROOM   (cid 3)
 *      -10500 .. -1900 (exclu)            → OCEAN      (cid 2)
 *      >= -1900                            → LAND       (cid 1)
 *
 * On n'échantillonne donc QUE la continentalness — pas toute la pile de biomes
 * — ce qui est bien plus rapide. Multi-thread via OpenMP, chaque thread a son
 * propre BiomeNoise (thread-safe). Top-K maintenu sous un verrou minimal.
 *
 * Pipeline : pour chaque seed, on échantillonne une grille de continentalness
 * couvrant la zone du dessin + une marge de glissement, on la classe en cids,
 * puis on fait une corrélation glissante (brute, mais la grille est petite) et
 * on garde le meilleur alignement. Le score = fraction de cellules PEINTES du
 * template qui correspondent.
 *
 * DSEARCH_BUILD_VERSION=4  (hotcold weighted fix + temp_grid)
 */
#if defined(_WIN32)
#  define EXPORT __declspec(dllexport)
#else
#  define EXPORT __attribute__((visibility("default")))
#endif

#include "biomenoise.h"
#include "generator.h"
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <float.h>

#ifdef _OPENMP
#  include <omp.h>
#endif

/* cids — doivent rester synchronisés avec drawing_search.py */
#define CID_ANY       0
#define CID_LAND      1
#define CID_OCEAN     2
#define CID_MUSHROOM  3
#define CID_HOT       4   /* temperature * 10000 >= 5500  */
#define CID_COLD      5   /* temperature * 10000 <= -4500 */

/* Seuils par défaut (échelle continentalness * 10000). Configurables depuis
 * Python via dsearch_set_thresholds(). */
static double g_thr_ocean    = -1900.0;   /* >= seuil_ocean → land */
static double g_thr_mushroom = -10500.0;  /* < seuil_mushroom → mushroom */

/* Seuils température (échelle temperature * 10000). */
#define THR_HOT   5500.0
#define THR_COLD -4500.0

/* Classifie une valeur de température en cid HOT/COLD/LAND (ou ANY si neutre). */
static inline int8_t classify_temp(double t10k)
{
    if (t10k >=  THR_HOT)  return CID_HOT;
    if (t10k <= THR_COLD)  return CID_COLD;
    return CID_LAND;   /* zone neutre : on la traite comme land (non-hot/cold) */
}

EXPORT void dsearch_set_thresholds(double ocean_thr, double mushroom_thr)
{
    g_thr_ocean    = ocean_thr;
    g_thr_mushroom = mushroom_thr;
}

/* Classe une valeur de continentalness brute (avant *10000) en cid. */
static inline int classify(double cont_raw)
{
    double v = cont_raw * 10000.0;
    if (v < g_thr_mushroom) return CID_MUSHROOM;
    if (v < g_thr_ocean)    return CID_OCEAN;
    return CID_LAND;
}

/* ───────────────────────────── État de recherche ───────────────────────── */
typedef struct {
    /* Template (dessin) */
    int      tw, th;            /* largeur / hauteur en cellules */
    int8_t  *tmpl;              /* tw*th cids (0=any) */
    int      n_painted;         /* nb de cellules peintes (cid != any) */
    int      n_land;            /* nb de cellules LAND dans le template */
    int      n_ocean;           /* nb de cellules OCEAN */
    int      n_mush;            /* nb de cellules MUSHROOM */
    int      n_temp;            /* nb de cellules HOT+COLD dans le template */
    int      weighted;          /* 1 = scoring équilibré par classe */
    int      cell_blocks;       /* blocs par cellule (taille de cellule) */

    /* Mode BIOME complet (hybride) : si use_biomes=1, le template contient des
     * biome IDs (tmpl_bio) et on génère les biomes complets via getBiomeAt au
     * lieu de classer par continentalness. Plus lent mais nécessaire pour les
     * biomes spécifiques (jungle, désert...). */
    int      use_biomes;        /* 0 = continentalness (rapide), 1 = biomes complets */
    int     *tmpl_bio;          /* tw*th biome IDs canoniques (-1 = any), si use_biomes */
    int     *bio_remap;         /* table 256 : biome_id réel → id canonique (groupes) */
    int      bio_remap_n;       /* taille de bio_remap (0 = pas de remap) */
    int      use_temperature;   /* 2 = temperature noise (Hot/Cold), rapide comme continental */
    /* Origine d'échantillonnage (renseignée au début de run_batch*), pour
     * convertir un offset (cellules fines) en coordonnées monde EXACTES. */
    int      samp_gx0, samp_gz0;   /* coin monde de la cellule fine (0,0) du template */

    /* Recherche */
    int      mc;                /* version cubiomes */
    int      large;             /* large biomes ? */
    int      slide_r;           /* rayon de glissement (cellules) */
    int      center_x, center_z;/* centre de la zone (blocs) */
    int64_t  start_seed;        /* seed de départ */
    double   prefilter_thr;     /* seuil de l'étage 1 (0..1), adaptatif */
    double   floor_thr;         /* plancher : prefilter_thr ne descend jamais sous ça */
    int      prefilter_octaves; /* nb d'octaves du pré-filtre (ex: 2). -1 = full */

    /* Top-K (protégé par lock) */
    int      top_k;
    double  *top_scores;        /* top_k scores triés décroissants */
    int64_t *top_seeds;         /* seeds correspondantes */
    int      *top_offx;         /* offsets du meilleur alignement */
    int      *top_offz;
    int      top_count;         /* nb d'entrées remplies */

    /* Compteurs (atomiques / sous lock) */
    volatile int64_t tested;
    volatile int     stop;      /* drapeau d'arrêt */
    volatile int64_t next_index;/* distribution des seeds */

#ifdef _OPENMP
    omp_lock_t lock;
#endif
} DSearch;

static DSearch *g_search = NULL;

static inline void ds_lock(DSearch *s) {
#ifdef _OPENMP
    omp_set_lock(&s->lock);
#else
    (void)s;
#endif
}
static inline void ds_unlock(DSearch *s) {
#ifdef _OPENMP
    omp_unset_lock(&s->lock);
#else
    (void)s;
#endif
}

/* Échantillonne une grille de continentalness (W*H) et la classe en cids.
 * bn : BiomeNoise déjà seedé sur NP_CONTINENTALNESS.
 * gx,gz : coin haut-gauche en BLOCS. cells couvre cell_blocks blocs chacune.
 * out : W*H int8 (cids). */
static void sample_class_grid(const BiomeNoise *bn, int8_t *out,
                              int gx, int gz, int W, int H,
                              int cell_blocks)
{
    /* La continentalness s'échantillonne à scale=4 (convention cubiomes :
     * x_noise = x_bloc / 4). On prend le centre de chaque cellule. */
    for (int zi = 0; zi < H; zi++) {
        double zb = (double)(gz + (int64_t)zi * cell_blocks + cell_blocks / 2);
        double zn = zb * 0.25;
        for (int xi = 0; xi < W; xi++) {
            double xb = (double)(gx + (int64_t)xi * cell_blocks + cell_blocks / 2);
            double xn = xb * 0.25;
            double c = sampleClimatePara((BiomeNoise *)bn, NULL, xn, zn);
            out[zi * W + xi] = (int8_t)classify(c);
        }
    }
}

/* Score d'alignement à UN offset donné (dx,dz) dans la grille `big`
 * ((tw+2r)*(th+2r)). Renvoie la fraction de cellules peintes concordantes. */
/* Score d'alignement à UN offset donné (dx,dz) dans la grille `big`.
 * Si weighted=1, le score est la MOYENNE des taux de match par classe (terre,
 * océan, mushroom), ce qui empêche l'océan de fond de dominer le score. Sinon
 * c'est le taux brut de cellules peintes concordantes. */
static double align_score_w(const int8_t *tmpl, int tw, int th,
                            const int8_t *big, int bw, int dx, int dz,
                            int weighted, int n_painted,
                            int n_land, int n_ocean, int n_mush, int n_temp)
{
    if (n_painted == 0) return 0.0;
    if (!weighted) {
        int count = 0;
        for (int j = 0; j < th; j++) {
            const int8_t *trow = tmpl + (size_t)j * tw;
            const int8_t *brow = big + (size_t)(dz + j) * bw + dx;
            for (int i = 0; i < tw; i++)
                if (trow[i] != CID_ANY && trow[i] == brow[i]) count++;
        }
        return (double)count / (double)n_painted;
    }
    /* Mode pondéré : compte les matchs PAR classe. */
    int m_land = 0, m_ocean = 0, m_mush = 0, m_temp = 0;
    for (int j = 0; j < th; j++) {
        const int8_t *trow = tmpl + (size_t)j * tw;
        const int8_t *brow = big + (size_t)(dz + j) * bw + dx;
        for (int i = 0; i < tw; i++) {
            int8_t t = trow[i];
            if (t == CID_ANY || t != brow[i]) continue;
            if      (t == CID_LAND)     m_land++;
            else if (t == CID_OCEAN)    m_ocean++;
            else if (t == CID_MUSHROOM) m_mush++;
            else if (t == CID_HOT || t == CID_COLD) m_temp++;
        }
    }
    /* Score brut (taux global de match). */
    double raw = (double)(m_land + m_ocean + m_mush + m_temp) / (double)n_painted;
    /* Taux de match des classes "forme" (terre + mushroom + hot + cold).
     * Hot/Cold comptent comme "forme" : ils ne peuvent pas dominer par défaut
     * comme l'océan, donc on les inclut dans n_shape. */
    int n_shape = n_land + n_mush + n_temp;
    int m_shape = m_land + m_mush + m_temp;
    if (n_shape == 0) return raw;   /* dessin sans forme → score brut */
    double shape_rate = (double)m_shape / (double)n_shape;
    /* Score = mix doux : on garde l'essentiel du score brut mais on tire vers
     * le taux de la forme, pour qu'une zone "tout océan" (forme=0%) ne puisse
     * pas obtenir un score élevé juste en matchant l'océan de fond.
     * poids 0.5/0.5 : équilibré et lisible. */
    return 0.5 * raw + 0.5 * shape_rate;
}

static double align_score_at(const int8_t *tmpl, int tw, int th, int n_painted,
                             const int8_t *big, int bw, int dx, int dz)
{
    DSearch *gs = g_search;
    int wt = gs ? gs->weighted : 0;
    int nl = gs ? gs->n_land : 0, no = gs ? gs->n_ocean : 0;
    int nm = gs ? gs->n_mush : 0, nt = gs ? gs->n_temp : 0;
    return align_score_w(tmpl, tw, th, big, bw, dx, dz, wt, n_painted, nl, no, nm, nt);
}

/* Pré-filtre rapide : teste l'offset central et ses 8 voisins immédiats
 * (±1 cellule grossière), garde le meilleur. Tolérant au léger décalage sans
 * payer le glissement complet. gr = rayon de glissement (offset central). */
static double prefilter_quick(const int8_t *tmpl, int tw, int th, int n_painted,
                              const int8_t *big, int bw, int gr)
{
    double best = 0.0;
    int span = gr < 2 ? gr : 2;   /* voisinage ±2 max */
    for (int dz = gr - span; dz <= gr + span; dz++) {
        for (int dx = gr - span; dx <= gr + span; dx++) {
            double sc = align_score_at(tmpl, tw, th, n_painted, big, bw, dx, dz);
            if (sc > best) best = sc;
        }
    }
    return best;
}

/* Corrélation glissante : trouve le meilleur offset (dx,dz) ∈ [-r,r]² pour
 * aligner tmpl (tw*th) sur big ((tw+2r)*(th+2r)), maximise le nb de cellules
 * peintes concordantes. Renvoie le meilleur score (0..1) et l'offset.
 *
 * OPTIMISATION HIÉRARCHIQUE (coarse-to-fine) : la corrélation entre une forme
 * et la carte est globalement lisse. Au lieu de tester les D² offsets, on teste
 * d'abord une grille grossière (pas = cstep), puis on raffine finement autour
 * du meilleur. Réduit ~D²/cstep² + petit raffinement → souvent 5-10× moins de
 * positions. Pour les petits r on reste en force brute (overhead non rentable). */
static double best_alignment(const int8_t *tmpl, int tw, int th, int n_painted,
                             const int8_t *big, int r,
                             int *best_dx, int *best_dz)
{
    int bw = tw + 2 * r;
    int D = 2 * r + 1;
    double best = -1.0; int bdx = 0, bdz = 0;

    if (n_painted == 0) { *best_dx = 0; *best_dz = 0; return 0.0; }

    /* Petits r : force brute directe (overhead hiérarchique non rentable). */
    if (D <= 7) {
        for (int dz = 0; dz < D; dz++)
            for (int dx = 0; dx < D; dx++) {
                double sc = align_score_at(tmpl, tw, th, n_painted, big, bw, dx, dz);
                if (sc > best) {
                    best = sc; bdx = dx; bdz = dz;
                    if (sc >= 1.0) { *best_dx=dx-r; *best_dz=dz-r; return 1.0; }
                }
            }
        *best_dx = bdx - r; *best_dz = bdz - r;
        return best;
    }

    /* Phase grossière : balaye avec un pas de cstep. */
    int cstep = 3;
    for (int dz = 0; dz < D; dz += cstep) {
        for (int dx = 0; dx < D; dx += cstep) {
            double sc = align_score_at(tmpl, tw, th, n_painted, big, bw, dx, dz);
            if (sc > best) { best = sc; bdx = dx; bdz = dz; }
        }
    }
    /* Phase fine : raffine dans une fenêtre ±cstep autour du meilleur grossier. */
    int z0 = bdz - cstep; if (z0 < 0) z0 = 0;
    int z1 = bdz + cstep; if (z1 > D - 1) z1 = D - 1;
    int x0 = bdx - cstep; if (x0 < 0) x0 = 0;
    int x1 = bdx + cstep; if (x1 > D - 1) x1 = D - 1;
    for (int dz = z0; dz <= z1; dz++) {
        for (int dx = x0; dx <= x1; dx++) {
            double sc = align_score_at(tmpl, tw, th, n_painted, big, bw, dx, dz);
            if (sc > best) {
                best = sc; bdx = dx; bdz = dz;
                if (sc >= 1.0) { *best_dx=dx-r; *best_dz=dz-r; return 1.0; }
            }
        }
    }
    *best_dx = bdx - r; *best_dz = bdz - r;
    return best;
}

/* Insère un résultat dans le top-K (sous lock). */
static void consider(DSearch *s, double score, int64_t seed, int dx, int dz)
{
    ds_lock(s);
    /* anti-doublon de seed */
    for (int i = 0; i < s->top_count; i++) {
        if (s->top_seeds[i] == seed) { ds_unlock(s); return; }
    }
    if (s->top_count < s->top_k) {
        /* insertion triée */
        int pos = s->top_count;
        while (pos > 0 && s->top_scores[pos-1] < score) {
            s->top_scores[pos] = s->top_scores[pos-1];
            s->top_seeds[pos]  = s->top_seeds[pos-1];
            s->top_offx[pos]   = s->top_offx[pos-1];
            s->top_offz[pos]   = s->top_offz[pos-1];
            pos--;
        }
        s->top_scores[pos] = score; s->top_seeds[pos] = seed;
        s->top_offx[pos] = dx; s->top_offz[pos] = dz;
        s->top_count++;
    } else if (score > s->top_scores[s->top_k - 1]) {
        /* remplace le pire et ré-insère trié */
        int pos = s->top_k - 1;
        while (pos > 0 && s->top_scores[pos-1] < score) {
            s->top_scores[pos] = s->top_scores[pos-1];
            s->top_seeds[pos]  = s->top_seeds[pos-1];
            s->top_offx[pos]   = s->top_offx[pos-1];
            s->top_offz[pos]   = s->top_offz[pos-1];
            pos--;
        }
        s->top_scores[pos] = score; s->top_seeds[pos] = seed;
        s->top_offx[pos] = dx; s->top_offz[pos] = dz;
    }
    /* Seuil adaptatif : une fois le top-K plein, on remonte le pré-filtre
     * juste sous le PIRE score du top, avec une marge de sécurité. Le pré-filtre
     * utilise PEU d'octaves (approximatif) et teste peu de positions, donc il
     * peut sous-estimer le vrai score fin → marge large (0.22) pour ne pas
     * rejeter un candidat dont le score fin réel dépasserait l'estimation. */
    if (s->top_count >= s->top_k) {
        double worst = s->top_scores[s->top_k - 1];
        double cand_thr = worst - 0.10;
        if (cand_thr > s->prefilter_thr) {
            s->prefilter_thr = cand_thr;
        }
        if (s->prefilter_thr < s->floor_thr)
            s->prefilter_thr = s->floor_thr;
    }
    ds_unlock(s);
}

/* ───────────────────────────── API exportée ────────────────────────────── */

/* Crée le contexte de recherche. tmpl est copié. Retourne 0 si OK. */
EXPORT int dsearch_init(const int8_t *tmpl, int tw, int th, int cell_blocks,
                        int mc, int large, int slide_r,
                        int center_x, int center_z,
                        int64_t start_seed, int top_k, double prefilter_thr,
                        int prefilter_octaves, int weighted)
{
    if (g_search) return -1; /* déjà initialisé : appeler dsearch_free d'abord */
    DSearch *s = (DSearch *)calloc(1, sizeof(DSearch));
    if (!s) return -2;
    s->tw = tw; s->th = th; s->cell_blocks = cell_blocks;
    s->mc = mc; s->large = large; s->slide_r = slide_r;
    s->center_x = center_x; s->center_z = center_z;
    s->start_seed = start_seed; s->top_k = top_k;
    s->prefilter_thr = prefilter_thr;
    s->floor_thr = prefilter_thr;
    s->prefilter_octaves = (prefilter_octaves == 0) ? 2 : prefilter_octaves;
    s->weighted = weighted;

    s->tmpl = (int8_t *)malloc((size_t)tw * th);
    if (!s->tmpl) { free(s); return -3; }
    memcpy(s->tmpl, tmpl, (size_t)tw * th);
    s->n_painted = 0; s->n_land = 0; s->n_ocean = 0; s->n_mush = 0; s->n_temp = 0;
    for (int i = 0; i < tw * th; i++) {
        int8_t v = s->tmpl[i];
        if (v == CID_ANY) continue;
        s->n_painted++;
        if      (v == CID_LAND)     s->n_land++;
        else if (v == CID_OCEAN)    s->n_ocean++;
        else if (v == CID_MUSHROOM) s->n_mush++;
        else if (v == CID_HOT || v == CID_COLD) s->n_temp++;
    }

    s->top_scores = (double *)calloc(top_k, sizeof(double));
    s->top_seeds  = (int64_t *)calloc(top_k, sizeof(int64_t));
    s->top_offx   = (int *)calloc(top_k, sizeof(int));
    s->top_offz   = (int *)calloc(top_k, sizeof(int));
    s->top_count = 0;
    s->tested = 0; s->stop = 0; s->next_index = 0;
#ifdef _OPENMP
    omp_init_lock(&s->lock);
#endif
    g_search = s;
    return 0;
}

/* Init du mode BIOME complet. tmpl_bio = tw*th biome IDs (-1 = any). Active la
 * génération complète des biomes (getBiomeAt) au lieu de la continentalness. */
EXPORT int dsearch_init_biomes(const int *tmpl_bio, int tw, int th, int cell_blocks,
                               int mc, int large, int slide_r,
                               int center_x, int center_z,
                               int64_t start_seed, int top_k,
                               const int *bio_remap, int bio_remap_n)
{
    if (g_search) return -1;
    DSearch *s = (DSearch *)calloc(1, sizeof(DSearch));
    if (!s) return -2;
    s->tw = tw; s->th = th; s->cell_blocks = cell_blocks;
    s->mc = mc; s->large = large; s->slide_r = slide_r;
    s->center_x = center_x; s->center_z = center_z;
    s->start_seed = start_seed; s->top_k = top_k;
    s->use_biomes = 1;
    s->tmpl_bio = (int *)malloc((size_t)tw * th * sizeof(int));
    if (!s->tmpl_bio) { free(s); return -3; }
    memcpy(s->tmpl_bio, tmpl_bio, (size_t)tw * th * sizeof(int));
    /* Table de remap optionnelle (groupes de biomes). */
    s->bio_remap = NULL; s->bio_remap_n = 0;
    if (bio_remap && bio_remap_n > 0) {
        s->bio_remap = (int *)malloc((size_t)bio_remap_n * sizeof(int));
        if (s->bio_remap) {
            memcpy(s->bio_remap, bio_remap, (size_t)bio_remap_n * sizeof(int));
            s->bio_remap_n = bio_remap_n;
        }
    }
    s->n_painted = 0;
    for (int i = 0; i < tw * th; i++) if (s->tmpl_bio[i] >= 0) s->n_painted++;
    s->top_scores = (double *)calloc(top_k, sizeof(double));
    s->top_seeds  = (int64_t *)calloc(top_k, sizeof(int64_t));
    s->top_offx   = (int *)calloc(top_k, sizeof(int));
    s->top_offz   = (int *)calloc(top_k, sizeof(int));
    s->top_count = 0; s->tested = 0; s->stop = 0; s->next_index = 0;
#ifdef _OPENMP
    omp_init_lock(&s->lock);
#endif
    g_search = s;
    return 0;
}
static void sample_coarse_padded(const BiomeNoise *bn, int8_t *out,
                                 int gx, int gz, int cw, int ch,
                                 int cell_blocks, int step)
{
    int step_blocks = step * cell_blocks;
    for (int zi = 0; zi < ch; zi++) {
        double zb = (double)(gz + (int64_t)zi * step_blocks + step_blocks / 2);
        double zn = zb * 0.25;
        for (int xi = 0; xi < cw; xi++) {
            double xb = (double)(gx + (int64_t)xi * step_blocks + step_blocks / 2);
            double xn = xb * 0.25;
            double c = sampleClimatePara((BiomeNoise *)bn, NULL, xn, zn);
            out[zi * cw + xi] = (int8_t)classify(c);
        }
    }
}

/* Pré-filtre TOLÉRANT : glisse le template grossier (tcw*tch) sur la grille
 * grossière élargie (cw*ch), teste tous les offsets et garde le meilleur score.
 * C'est ce qui évite de rejeter une bonne forme simplement décalée. */
static double prefilter_best(const int8_t *tmpl_coarse, int tcw, int tch,
                             int n_painted_coarse,
                             const int8_t *cand, int cw, int ch)
{
    if (n_painted_coarse == 0) return 0.0;
    int best = 0;
    int max_dz = ch - tch, max_dx = cw - tcw;
    for (int dz = 0; dz <= max_dz; dz++) {
        for (int dx = 0; dx <= max_dx; dx++) {
            int count = 0;
            for (int j = 0; j < tch; j++) {
                const int8_t *trow = tmpl_coarse + (size_t)j * tcw;
                const int8_t *crow = cand + (size_t)(dz + j) * cw + dx;
                for (int i = 0; i < tcw; i++) {
                    int8_t t = trow[i];
                    if (t != CID_ANY && t == crow[i]) count++;
                }
            }
            if (count > best) best = count;
        }
    }
    return (double)best / (double)n_painted_coarse;
}

/* ─────────────────────────── MODE BIOME COMPLET ──────────────────────────
 * Pour les biomes spécifiques (jungle, désert...), terre/océan/mushroom ne
 * suffit pas : il faut le biome exact, qui dépend de tous les paramètres
 * climatiques. On utilise getBiomeAt() du Generator cubiomes. C'est plus lent
 * (~10-50× la continentalness) mais nécessaire. Activé seulement si l'utilisateur
 * peint au moins un biome spécifique (sinon on reste sur le chemin rapide). */

/* Échantillonne une grille de BIOME IDs via getBiomeAt. out = W*H ints.
 * Si remap != NULL, chaque biome est remappé vers son id canonique (groupes). */
static void sample_biome_grid(Generator *g, int *out,
                              int gx, int gz, int W, int H,
                              int cell_blocks, int step,
                              const int *remap, int remap_n)
{
    int step_blocks = step * cell_blocks;
    for (int zi = 0; zi < H; zi++) {
        int zb = gz + zi * step_blocks + step_blocks / 2;
        for (int xi = 0; xi < W; xi++) {
            int xb = gx + xi * step_blocks + step_blocks / 2;
            int b = getBiomeAt(g, 4, xb >> 2, 319 >> 2, zb >> 2);
            if (remap && b >= 0 && b < remap_n && remap[b] >= 0)
                b = remap[b];
            out[zi * W + xi] = b;
        }
    }
}

/* Score d'alignement biome à un offset (dx,dz). Compte les cellules peintes
 * dont le biome correspond exactement. */
static double biome_score_at(const int *tmpl, int tw, int th, int n_painted,
                             const int *big, int bw, int dx, int dz)
{
    if (n_painted == 0) return 0.0;
    int count = 0;
    for (int j = 0; j < th; j++) {
        const int *trow = tmpl + (size_t)j * tw;
        const int *brow = big + (size_t)(dz + j) * bw + dx;
        for (int i = 0; i < tw; i++)
            if (trow[i] >= 0 && trow[i] == brow[i]) count++;
    }
    return (double)count / (double)n_painted;
}

/* Meilleur alignement biome (force brute hiérarchique, comme best_alignment). */
static double biome_best_alignment(const int *tmpl, int tw, int th, int n_painted,
                                   const int *big, int r, int *bdx, int *bdz)
{
    int bw = tw + 2 * r, D = 2 * r + 1;
    double best = -1.0; int bx = 0, bz = 0;
    if (n_painted == 0) { *bdx = 0; *bdz = 0; return 0.0; }
    int cstep = (D > 9) ? 3 : 1;
    for (int dz = 0; dz < D; dz += cstep)
        for (int dx = 0; dx < D; dx += cstep) {
            double sc = biome_score_at(tmpl, tw, th, n_painted, big, bw, dx, dz);
            if (sc > best) { best = sc; bx = dx; bz = dz; }
        }
    if (cstep > 1) {
        int z0 = bz-cstep<0?0:bz-cstep, z1 = bz+cstep>D-1?D-1:bz+cstep;
        int x0 = bx-cstep<0?0:bx-cstep, x1 = bx+cstep>D-1?D-1:bx+cstep;
        for (int dz = z0; dz <= z1; dz++)
            for (int dx = x0; dx <= x1; dx++) {
                double sc = biome_score_at(tmpl, tw, th, n_painted, big, bw, dx, dz);
                if (sc > best) { best = sc; bx = dx; bz = dz; }
            }
    }
    *bdx = bx - r; *bdz = bz - r;
    return best;
}

/* Worker du mode biome complet. Plus lent : getBiomeAt sur toute la grille. */
static int64_t run_batch_biomes(DSearch *s, int64_t start, int64_t end, int n_threads)
{
#ifdef _OPENMP
    if (n_threads > 0) omp_set_num_threads(n_threads);
#endif
    int tw = s->tw, th = s->th, r = s->slide_r;
    int step = 2;
    int gtw = (tw + step - 1) / step, gth = (th + step - 1) / step;
    /* Glissement en mode biome : on le PLAFONNE. Un glissement trop large permet
     * de "trouver" un petit patch du biome n'importe où → faux 100%. On limite
     * le rayon à ~1/4 de la taille du dessin (en cellules grossières), min 1. */
    int gr = (r + step - 1) / step;
    int gr_max = (gtw < gth ? gtw : gth) / 4;
    if (gr_max < 1) gr_max = 1;
    if (gr > gr_max) gr = gr_max;
    if (gr < 1) gr = 1;
    int gw = gtw + 2*gr, gh = gth + 2*gr;

    /* Template biome sous-échantillonné. */
    int *gtmpl = (int *)malloc((size_t)gtw * gth * sizeof(int));
    int gpainted = 0;
    for (int zi = 0; zi < gth; zi++)
        for (int xi = 0; xi < gtw; xi++) {
            int v = s->tmpl_bio[(zi*step < th ? zi*step : th-1) * tw
                                + (xi*step < tw ? xi*step : tw-1)];
            gtmpl[zi*gtw + xi] = v;
            if (v >= 0) gpainted++;
        }
    int step_blocks = step * s->cell_blocks;
    int half_w = (tw * s->cell_blocks) / 2, half_h = (th * s->cell_blocks) / 2;
    int ggx0 = s->center_x - half_w - gr * step_blocks;
    int ggz0 = s->center_z - half_h - gr * step_blocks;
    s->samp_gx0 = ggx0 + gr * step_blocks + s->cell_blocks / 2;  /* origine = centre de la cellule FINE (0,0), aligné avec la convention Python (coin = origine - cell/2) */
    s->samp_gz0 = ggz0 + gr * step_blocks + s->cell_blocks / 2;

    #pragma omp parallel
    {
        Generator gen;
        setupGenerator(&gen, s->mc, s->large ? 0x1 /*LARGE_BIOMES*/ : 0);
        int *grid = (int *)malloc((size_t)gw * gh * sizeof(int));
        int64_t local_tested = 0;

        #pragma omp for schedule(dynamic, 64)
        for (int64_t idx = start; idx < end; idx++) {
            if (s->stop) continue;
            int64_t seed = s->start_seed + idx;
            local_tested++;
            applySeed(&gen, 0 /*DIM_OVERWORLD*/, (uint64_t)seed);
            sample_biome_grid(&gen, grid, ggx0, ggz0, gw, gh, s->cell_blocks, step,
                              s->bio_remap, s->bio_remap_n);
            int gdx, gdz;
            double score = biome_best_alignment(gtmpl, gtw, gth, gpainted,
                                                grid, gr, &gdx, &gdz);
            consider(s, score, seed, gdx * step, gdz * step);
        }
        #pragma omp atomic
        s->tested += local_tested;
        free(grid);
    }
    free(gtmpl);
    s->next_index = end;
    return end - start;
}

/* Lance la recherche sur `batch` seeds avec `n_threads` threads OpenMP.
 * Pipeline à DEUX ÉTAGES :
 *   1) pré-filtre : grille sous-échantillonnée (step=2), alignement fixe. Rapide.
 *   2) si score pré-filtre >= prefilter_thr : grille fine + glissement complet.
 * BLOQUE jusqu'à ce que le batch soit traité ou stop demandé.
 * Retourne le nb de seeds testées. */
EXPORT int64_t dsearch_run_batch(int64_t batch, int n_threads)
{
    DSearch *s = g_search;
    if (!s) return -1;
    /* Mode biome complet : chemin séparé (plus lent). */
    if (s->use_biomes && s->tmpl_bio) {
        int64_t start = s->next_index;
        return run_batch_biomes(s, start, start + batch, n_threads);
    }
    /* Mode TEMPERATURE : même pipeline que continentalness mais sur NP_TEMPERATURE. */
    if (s->use_temperature == 2) {
        int64_t start = s->next_index;
        int64_t end   = start + batch;
        s->next_index = end;
        int tw = s->tw, th = s->th, r = s->slide_r;
        int step = 2;
        int gtw = (tw + step - 1) / step;
        int gth = (th + step - 1) / step;
        int gr  = (r + step - 1) / step;
        if (gr < 1) gr = 1;
        int gw = gtw + 2 * gr;
        int gh = gth + 2 * gr;
        int half_w = (tw * s->cell_blocks) / 2;
        int half_h = (th * s->cell_blocks) / 2;
        int step_blocks = step * s->cell_blocks;
        int ggx0 = s->center_x - half_w - gr * step_blocks;
        int ggz0 = s->center_z - half_h - gr * step_blocks;
        s->samp_gx0 = ggx0 + gr * step_blocks + s->cell_blocks / 2;  /* origine = centre de la cellule FINE (0,0), aligné avec la convention Python (coin = origine - cell/2) */
        s->samp_gz0 = ggz0 + gr * step_blocks + s->cell_blocks / 2;

        /* Template grossier (step=2) avec cids HOT/COLD/LAND. */
        int8_t *gtmpl = (int8_t *)malloc((size_t)gtw * gth);
        int gpainted = 0;
        for (int j = 0; j < gth; j++) {
            int sj = j * step; if (sj >= th) sj = th - 1;
            for (int i = 0; i < gtw; i++) {
                int si = i * step; if (si >= tw) si = tw - 1;
                int8_t v = s->tmpl[sj * tw + si];
                gtmpl[j * gtw + i] = v;
                if (v != CID_ANY) gpainted++;
            }
        }
        if (gpainted == 0) { free(gtmpl); return 0; }

        #pragma omp parallel
        {
            BiomeNoise bn_pre;  memset(&bn_pre,  0, sizeof(bn_pre));
            BiomeNoise bn_full; memset(&bn_full, 0, sizeof(bn_full));
            int8_t *grid_pre  = (int8_t *)malloc((size_t)gw * gh);
            int8_t *grid_full = (int8_t *)malloc((size_t)gw * gh);
            int64_t local_tested = 0;

            #pragma omp for schedule(dynamic, 256)
            for (int64_t idx = start; idx < end; idx++) {
                if (s->stop) continue;
                int64_t seed = s->start_seed + idx;
                local_tested++;

                /* Pré-filtre : temperature en 2 octaves. */
                setClimateParaSeed(&bn_pre, (uint64_t)seed, s->large,
                                   NP_TEMPERATURE, s->prefilter_octaves);
                /* Sample grille grossière température. */
                for (int zi = 0; zi < gh; zi++) {
                    double zb = (double)(ggz0 + (int64_t)zi * step_blocks + step_blocks / 2);
                    double zn = zb * 0.25;
                    for (int xi = 0; xi < gw; xi++) {
                        double xb = (double)(ggx0 + (int64_t)xi * step_blocks + step_blocks / 2);
                        double xn = xb * 0.25;
                        double t = sampleClimatePara(&bn_pre, NULL, xn, zn);
                        grid_pre[zi * gw + xi] = classify_temp(t * 10000.0);
                    }
                }
                double quick = prefilter_quick(gtmpl, gtw, gth, gpainted,
                                               grid_pre, gw, gr);
                if (quick < s->prefilter_thr) continue;

                int pdx, pdz;
                best_alignment(gtmpl, gtw, gth, gpainted, grid_pre, gr, &pdx, &pdz);

                /* Raffinement full octaves. */
                int fine_r = 2;
                int fw = gtw + 2 * fine_r;
                int fh = gth + 2 * fine_r;
                int foff_x = (pdx + gr) - fine_r;
                int foff_z = (pdz + gr) - fine_r;
                int fgx0 = ggx0 + foff_x * step_blocks;
                int fgz0 = ggz0 + foff_z * step_blocks;
                setClimateParaSeed(&bn_full, (uint64_t)seed, s->large,
                                   NP_TEMPERATURE, -1);
                for (int zi = 0; zi < fh; zi++) {
                    double zb = (double)(fgz0 + (int64_t)zi * step_blocks + step_blocks / 2);
                    double zn = zb * 0.25;
                    for (int xi = 0; xi < fw; xi++) {
                        double xb = (double)(fgx0 + (int64_t)xi * step_blocks + step_blocks / 2);
                        double xn = xb * 0.25;
                        double t = sampleClimatePara(&bn_full, NULL, xn, zn);
                        grid_full[zi * fw + xi] = classify_temp(t * 10000.0);
                    }
                }
                int fdx, fdz;
                double score = best_alignment(gtmpl, gtw, gth, gpainted,
                                              grid_full, fine_r, &fdx, &fdz);
                int total_dx = (pdx + fdx) * step;
                int total_dz = (pdz + fdz) * step;
                consider(s, score, seed, total_dx, total_dz);
            }
            #pragma omp atomic
            s->tested += local_tested;
            free(grid_pre);
            free(grid_full);
        }
        free(gtmpl);
        return 0;
    }
    int64_t start = s->next_index;
    int64_t end   = start + batch;

#ifdef _OPENMP
    if (n_threads > 0) omp_set_num_threads(n_threads);
#endif

    int tw = s->tw, th = s->th, r = s->slide_r;

    /* ── STRATÉGIE "SAMPLE ONCE" ──────────────────────────────────────────
     * La continentalness est un bruit LISSE : l'échantillonner à pleine
     * résolution (1 point/cellule) est inutile. On échantillonne à step=2
     * (1 point sur 2 sur chaque axe → 4× moins de points), UNE seule fois par
     * seed, sur une zone couvrant le dessin + la marge de glissement. Cette
     * grille unique sert au pré-filtre ET au scoring fin (qui glisse dessus,
     * sans re-échantillonner). Le template est aussi sous-échantillonné à step=2.
     */
    int step = 2;
    int gtw = (tw + step - 1) / step;   /* template grossi : largeur */
    int gth = (th + step - 1) / step;
    int gr  = (r + step - 1) / step;    /* rayon de glissement en cellules grossières */
    if (gr < 1) gr = 1;
    int gw = gtw + 2 * gr;              /* grille échantillonnée : largeur */
    int gh = gth + 2 * gr;

    /* Template sous-échantillonné (step), calculé 1×. */
    int8_t *gtmpl = (int8_t *)malloc((size_t)gtw * gth);
    int gpainted = 0;
    for (int zi = 0; zi < gth; zi++)
        for (int xi = 0; xi < gtw; xi++) {
            int8_t v = s->tmpl[(zi*step < th ? zi*step : th-1) * tw
                               + (xi*step < tw ? xi*step : tw-1)];
            gtmpl[zi*gtw + xi] = v;
            if (v != CID_ANY) gpainted++;
        }

    int step_blocks = step * s->cell_blocks;
    int half_w = (tw * s->cell_blocks) / 2;
    int half_h = (th * s->cell_blocks) / 2;
    int ggx0 = s->center_x - half_w - gr * step_blocks;
    int ggz0 = s->center_z - half_h - gr * step_blocks;
    /* Coin monde de la cellule FINE (0,0) du template à offset 0 : c'est
     * l'échantillon grossier (gr,gr), pris au centre de la cellule. Sert à
     * convertir l'offset retourné en coordonnées monde exactes. */
    s->samp_gx0 = ggx0 + gr * step_blocks + s->cell_blocks / 2;  /* origine = centre de la cellule FINE (0,0), aligné avec la convention Python (coin = origine - cell/2) */
    s->samp_gz0 = ggz0 + gr * step_blocks + s->cell_blocks / 2;
    int pre_oct = s->prefilter_octaves;  /* octaves du pré-filtre (ex: 2) */

    #pragma omp parallel
    {
        /* ── STRATÉGIE À DEUX NIVEAUX D'OCTAVES ──────────────────────────────
         * La continentalness a 9 octaves. Les premières (basse fréquence)
         * dominent la forme terre/mer à grande échelle. On utilise donc :
         *   - bn_pre : peu d'octaves (nmax=pre_oct) → ~5-10× plus rapide, sert
         *     au PRÉ-FILTRE grossier. Approximatif mais suffisant pour rejeter.
         *   - bn_full : toutes les octaves (nmax=-1) → exact, pour le SCORING
         *     fin des seeds qui passent le pré-filtre.
         * Chaque thread a ses deux BiomeNoise (thread-safe). */
        BiomeNoise bn_pre;  memset(&bn_pre, 0, sizeof(bn_pre));
        BiomeNoise bn_full; memset(&bn_full, 0, sizeof(bn_full));
        int8_t *grid_pre  = (int8_t *)malloc((size_t)gw * gh);
        int8_t *grid_full = (int8_t *)malloc((size_t)gw * gh);
        int64_t local_tested = 0;

        #pragma omp for schedule(dynamic, 256)
        for (int64_t idx = start; idx < end; idx++) {
            if (s->stop) continue;
            int64_t seed = s->start_seed + idx;
            local_tested++;

            /* ── Étage 1a : REJET RAPIDE (quelques positions seulement) ──
             * Test central + petit voisinage en peu d'octaves. La majorité des
             * seeds est rejetée ici sans payer le balayage complet. */
            setClimateParaSeed(&bn_pre, (uint64_t)seed, s->large,
                               NP_CONTINENTALNESS, pre_oct);
            sample_coarse_padded(&bn_pre, grid_pre, ggx0, ggz0, gw, gh,
                                 s->cell_blocks, step);
            double quick = prefilter_quick(gtmpl, gtw, gth, gpainted,
                                           grid_pre, gw, gr);
            if (quick < s->prefilter_thr) continue;

            /* ── Étage 1b : localisation grossière (balayage complet, peu
             * d'octaves, sur la grille DÉJÀ échantillonnée) ── */
            int pdx, pdz;
            best_alignment(gtmpl, gtw, gth, gpainted, grid_pre, gr, &pdx, &pdz);

            /* ── Étage 2 : raffinement LOCAL en TOUTES les octaves ──
             * Le pré-filtre a localisé la forme à l'offset (pdx,pdz). On
             * ré-échantillonne en full octaves UNIQUEMENT une petite fenêtre
             * autour de cette position (taille template + marge fine), puis on
             * cherche le meilleur offset fin dans cette fenêtre. Bien moins de
             * points full-octave que de re-sampler toute la zone. */
            int fine_r = 2;  /* marge de raffinement (cellules grossières) */
            int fw = gtw + 2 * fine_r;
            int fh = gth + 2 * fine_r;
            /* coin monde de la fenêtre fine : centre + offset pré-filtre - marge */
            int foff_x = (pdx + gr) - fine_r;  /* en cellules grossières depuis ggx0 */
            int foff_z = (pdz + gr) - fine_r;
            int fgx0 = ggx0 + foff_x * step_blocks;
            int fgz0 = ggz0 + foff_z * step_blocks;
            setClimateParaSeed(&bn_full, (uint64_t)seed, s->large,
                               NP_CONTINENTALNESS, -1);
            sample_coarse_padded(&bn_full, grid_full, fgx0, fgz0, fw, fh,
                                 s->cell_blocks, step);
            int fdx, fdz;
            double score = best_alignment(gtmpl, gtw, gth, gpainted,
                                          grid_full, fine_r, &fdx, &fdz);
            /* offset total en cellules fines = (pré-filtre + raffinement) × step */
            int total_dx = (pdx + fdx) * step;
            int total_dz = (pdz + fdz) * step;
            consider(s, score, seed, total_dx, total_dz);
        }
        #pragma omp atomic
        s->tested += local_tested;
        free(grid_pre);
        free(grid_full);
    }
    free(gtmpl);
    s->next_index = end;
    return end - start;
}

/* Lit le top-K courant. Les buffers (fournis par Python) reçoivent au plus
 * top_k entrées. Retourne le nb d'entrées écrites. */
/* Init du mode TEMPERATURE (Hot/Cold). Même pipeline que continentalness
 * mais échantillonne NP_TEMPERATURE et classe via classify_temp(). */
EXPORT int dsearch_init_temperature(const int8_t *tmpl, int tw, int th, int cell_blocks,
                                    int mc, int large, int slide_r,
                                    int center_x, int center_z,
                                    int64_t start_seed, int top_k,
                                    double prefilter_thr, int weighted)
{
    if (g_search) return -1;
    DSearch *s = (DSearch *)calloc(1, sizeof(DSearch));
    if (!s) return -2;
    s->tw = tw; s->th = th; s->cell_blocks = cell_blocks;
    s->mc = mc; s->large = large; s->slide_r = slide_r;
    s->center_x = center_x; s->center_z = center_z;
    s->start_seed = start_seed; s->top_k = top_k;
    s->prefilter_thr = prefilter_thr;
    s->floor_thr = prefilter_thr;
    s->prefilter_octaves = 2;
    s->weighted = weighted;
    s->use_temperature = 2;

    s->tmpl = (int8_t *)malloc((size_t)tw * th);
    if (!s->tmpl) { free(s); return -3; }
    memcpy(s->tmpl, tmpl, (size_t)tw * th);

    s->n_painted = 0;
    for (int i = 0; i < tw * th; i++) {
        if (s->tmpl[i] != CID_ANY) s->n_painted++;
    }

    s->top_scores = (double *)calloc(top_k, sizeof(double));
    s->top_seeds  = (int64_t *)calloc(top_k, sizeof(int64_t));
    s->top_offx   = (int *)calloc(top_k, sizeof(int));
    s->top_offz   = (int *)calloc(top_k, sizeof(int));
    s->top_count = 0;
    s->tested = 0; s->stop = 0; s->next_index = 0;
#ifdef _OPENMP
    omp_init_lock(&s->lock);
#endif
    g_search = s;
    return 0;
}

EXPORT int dsearch_get_top(int64_t *seeds, double *scores,
                           int *offx, int *offz, int max_n)
{
    DSearch *s = g_search;
    if (!s) return 0;
    ds_lock(s);
    int n = s->top_count < max_n ? s->top_count : max_n;
    for (int i = 0; i < n; i++) {
        seeds[i]  = s->top_seeds[i];
        scores[i] = s->top_scores[i];
        if (offx) offx[i] = s->top_offx[i];
        if (offz) offz[i] = s->top_offz[i];
    }
    ds_unlock(s);
    return n;
}

EXPORT int64_t dsearch_tested(void)  { return g_search ? g_search->tested : 0; }

/* Retourne l'origine d'échantillonnage : coin monde de la cellule (0,0) du
 * template à offset 0, et la taille de cellule. Python calcule alors le coin
 * monde EXACT d'un résultat : world_x = samp_gx0 + offset_x * cell_blocks. */
EXPORT void dsearch_get_origin(int *gx0, int *gz0, int *cell_blocks)
{
    if (g_search) {
        *gx0 = g_search->samp_gx0;
        *gz0 = g_search->samp_gz0;
        *cell_blocks = g_search->cell_blocks;
    } else {
        *gx0 = 0; *gz0 = 0; *cell_blocks = 1;
    }
}
EXPORT void    dsearch_stop(void)    { if (g_search) g_search->stop = 1; }
EXPORT int     dsearch_build_version(void) { return 4; }  /* bump on ABI changes */

EXPORT void dsearch_free(void)
{
    DSearch *s = g_search;
    if (!s) return;
#ifdef _OPENMP
    omp_destroy_lock(&s->lock);
#endif
    free(s->tmpl); free(s->tmpl_bio); free(s->bio_remap);
    free(s->top_scores); free(s->top_seeds);
    free(s->top_offx); free(s->top_offz); free(s);
    g_search = NULL;
}

/* Échantillonne une grille de classes pour UNE seed (pour le rendu/preview
 * côté Python). out reçoit W*H int8. Pas besoin du contexte de recherche. */
EXPORT void dsearch_class_grid(int64_t seed, int mc, int large,
                               int gx_blocks, int gz_blocks,
                               int W, int H, int cell_blocks, int8_t *out)
{
    (void)mc;
    BiomeNoise bn;
    memset(&bn, 0, sizeof(bn));
    setClimateParaSeed(&bn, (uint64_t)seed, large, NP_CONTINENTALNESS, -1);
    sample_class_grid(&bn, out, gx_blocks, gz_blocks, W, H, cell_blocks);
}

/* Génère une grille de cids TEMPÉRATURE (CID_HOT/COLD/LAND) au coin (gx,gz),
 * même convention que le scoring température. out = W*H int8. */
EXPORT void dsearch_temp_grid(int64_t seed, int mc, int large,
                              int gx_blocks, int gz_blocks,
                              int W, int H, int cell_blocks, int8_t *out)
{
    (void)mc;
    BiomeNoise bn;
    memset(&bn, 0, sizeof(bn));
    setClimateParaSeed(&bn, (uint64_t)seed, large, NP_TEMPERATURE, -1);
    for (int zi = 0; zi < H; zi++) {
        double zb = (double)(gz_blocks + zi * cell_blocks + cell_blocks / 2);
        double zn = zb * 0.25;
        for (int xi = 0; xi < W; xi++) {
            double xb = (double)(gx_blocks + xi * cell_blocks + cell_blocks / 2);
            double xn = xb * 0.25;
            double t = sampleClimatePara(&bn, NULL, xn, zn);
            out[zi * W + xi] = classify_temp(t * 10000.0);
        }
    }
}

/* Génère une grille de BIOME IDs au coin (gx,gz), même convention que le
 * scoring biome (getBiomeAt scale 4, centre de cellule). Pour l'affichage
 * cohérent des résultats du mode biome. out = W*H ints. */
EXPORT void dsearch_biome_grid(int64_t seed, int mc, int large,
                               int gx_blocks, int gz_blocks,
                               int W, int H, int cell_blocks, int *out)
{
    Generator gen;
    setupGenerator(&gen, mc, large ? 0x1 : 0);
    applySeed(&gen, 0 /*DIM_OVERWORLD*/, (uint64_t)seed);
    for (int zi = 0; zi < H; zi++) {
        int zb = gz_blocks + zi * cell_blocks + cell_blocks / 2;
        for (int xi = 0; xi < W; xi++) {
            int xb = gx_blocks + xi * cell_blocks + cell_blocks / 2;
            out[zi * W + xi] = getBiomeAt(&gen, 4, xb >> 2, 319 >> 2, zb >> 2);
        }
    }
}

/* Biome ID at each (xs_s4[i], zs_s4[i]) sampled at a PER-CELL y (ys_s4[i], in
 * scale-4 units = blockY>>2). Same coord convention as the GPU biomemap, so it
 * overlays 1:1 on the map. Used to reveal biomes that only appear at the real
 * terrain surface height (e.g. exposed sulfur_caves in low terrain). */
EXPORT void biome_varY_grid_s4(int64_t seed, int mc, int large,
                               const int *xs_s4, const int *zs_s4,
                               const int *ys_s4, int n, int *out)
{
    Generator gen;
    setupGenerator(&gen, mc, large ? 0x1 : 0);
    applySeed(&gen, 0 /*DIM_OVERWORLD*/, (uint64_t)seed);
    for (int i = 0; i < n; i++)
        out[i] = getBiomeAt(&gen, 4, xs_s4[i], ys_s4[i], zs_s4[i]);
}
