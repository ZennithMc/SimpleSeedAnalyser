#include "biomenoise.h"
#include <limits.h>

#include "tables/btree18.h"
#include "tables/btree192.h"
#include "tables/btree19.h"
#include "tables/btree20.h"
#include "tables/btree21wd.h"
#include "tables/btree262.h"
#include "tables/btree263.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <float.h>


//==============================================================================
// Noise
//==============================================================================


void initSurfaceNoise(SurfaceNoise *sn, int dim, uint64_t seed)
{
    uint64_t s;
    setSeed(&s, seed);
    octaveInit(&sn->octmin, &s, sn->oct+0, -15, 16);
    octaveInit(&sn->octmax, &s, sn->oct+16, -15, 16);
    octaveInit(&sn->octmain, &s, sn->oct+32, -7, 8);
    if (dim == DIM_END)
    {
        sn->xzScale = 2.0;
        sn->yScale = 1.0;
        sn->xzFactor = 80;
        sn->yFactor = 160;
    }
    else // DIM_OVERWORLD
    {
        octaveInit(&sn->octsurf, &s, sn->oct+40, -3, 4);
        skipNextN(&s, 262*10);
        octaveInit(&sn->octdepth, &s, sn->oct+44, -15, 16);
        sn->xzScale = 0.9999999814507745;
        sn->yScale = 0.9999999814507745;
        sn->xzFactor = 80;
        sn->yFactor = 160;
    }
}

void initSurfaceNoiseBeta(SurfaceNoiseBeta *snb, uint64_t seed)
{
    uint64_t s;
    setSeed(&s, seed);

    octaveInitBeta(&snb->octmin, &s, snb->oct+0, 16, 684.412, 0.5, 1.0, 2.0);
    octaveInitBeta(&snb->octmax, &s, snb->oct+16, 16, 684.412, 0.5, 1.0, 2.0);
    octaveInitBeta(&snb->octmain, &s, snb->oct+32, 8, 684.412/80.0, 0.5, 1.0, 2.0);
    skipNextN(&s, 262*8);
    octaveInitBeta(&snb->octcontA, &s, snb->oct+40, 10, 1.121, 0.5, 1.0, 2.0);
    octaveInitBeta(&snb->octcontB, &s, snb->oct+50, 16, 200.0, 0.5, 1.0, 2.0);
}

double sampleSurfaceNoiseBetween(const SurfaceNoise *sn, int x, int y, int z,
    double noiseMin, double noiseMax)
{
    double persist, amp;
    double dx, dy, dz, sy;
    int i;

    double xzScale = 684.412 * sn->xzScale;
    double yScale = 684.412 * sn->yScale;
    double vmin = 0;
    double vmax = 0;

    persist = 1.0 / 32768.0;
    amp = 64.0;

    for (i = 15; i >= 0; i--)
    {
        dx = x * xzScale * persist;
        dz = z * xzScale * persist;
        sy = yScale * persist;
        dy = y * sy;

        vmin += samplePerlin(&sn->octmin.octaves[i], dx, dy, dz, sy, dy) * amp;
        vmax += samplePerlin(&sn->octmax.octaves[i], dx, dy, dz, sy, dy) * amp;
        if (vmin - amp > noiseMax && vmax - amp > noiseMax)
            return noiseMax;
        if (vmin + amp < noiseMin && vmax + amp < noiseMin)
            return noiseMin;

        amp *= 0.5;
        persist *= 2.0;
    }

    double xzStep = xzScale / sn->xzFactor;
    double yStep = yScale / sn->yFactor;
    double vmain = 0.5;

    persist = 1.0 / 128.0;
    amp = 0.05 * 128.0;

    for (i = 7; i >= 0; i--)
    {
        dx = x * xzStep * persist;
        dz = z * xzStep * persist;
        sy = yStep * persist;
        dy = y * sy;

        vmain += samplePerlin(&sn->octmain.octaves[i], dx, dy, dz, sy, dy) * amp;
        if (vmain - amp > 1) return vmax;
        if (vmain + amp < 0) return vmin;

        amp *= 0.5;
        persist *= 2.0;
    }

    return clampedLerp(vmain, vmin, vmax);
}

double sampleSurfaceNoise(const SurfaceNoise *sn, int x, int y, int z)
{
    double xzScale = 684.412 * sn->xzScale;
    double yScale = 684.412 * sn->yScale;
    double xzStep = xzScale / sn->xzFactor;
    double yStep = yScale / sn->yFactor;

    double minNoise = 0;
    double maxNoise = 0;
    double mainNoise = 0;
    double persist = 1.0;
    double contrib = 1.0;
    double dx, dy, dz, sy, ty;
    int i;

    for (i = 0; i < 16; i++)
    {
        dx = maintainPrecision(x * xzScale * persist);
        dy = maintainPrecision(y * yScale  * persist);
        dz = maintainPrecision(z * xzScale * persist);
        sy = yScale * persist;
        ty = y * sy;

        minNoise += samplePerlin(&sn->octmin.octaves[i], dx, dy, dz, sy, ty) * contrib;
        maxNoise += samplePerlin(&sn->octmax.octaves[i], dx, dy, dz, sy, ty) * contrib;

        if (i < 8)
        {
            dx = maintainPrecision(x * xzStep * persist);
            dy = maintainPrecision(y * yStep  * persist);
            dz = maintainPrecision(z * xzStep * persist);
            sy = yStep * persist;
            ty = y * sy;
            mainNoise += samplePerlin(&sn->octmain.octaves[i], dx, dy, dz, sy, ty) * contrib;
        }
        persist *= 0.5;
        contrib *= 2.0;
    }

    return clampedLerp(0.5 + 0.05*mainNoise, minNoise/512.0, maxNoise/512.0);
}

//==============================================================================
// Nether (1.16+) and End (1.9+) Biome Generation
//==============================================================================

void setNetherSeed(NetherNoise *nn, uint64_t seed)
{
    uint64_t s;
    setSeed(&s, seed);
    doublePerlinInit(&nn->temperature, &s, &nn->oct[0], &nn->oct[2], -7, 2);
    setSeed(&s, seed+1);
    doublePerlinInit(&nn->humidity, &s, &nn->oct[4], &nn->oct[6], -7, 2);
}

/* Gets the 3D nether biome at scale 1:4 (for 1.16+).
 */
int getNetherBiome(const NetherNoise *nn, int x, int y, int z, float *ndel)
{
    const float npoints[5][4] = {
        { 0,    0,      0,              nether_wastes       },
        { 0,   -0.5,    0,              soul_sand_valley    },
        { 0.4,  0,      0,              crimson_forest      },
        { 0,    0.5,    0.375*0.375,    warped_forest       },
        {-0.5,  0,      0.175*0.175,    basalt_deltas       },
    };

    y = 0;
    float temp = sampleDoublePerlin(&nn->temperature, x, y, z);
    float humidity = sampleDoublePerlin(&nn->humidity, x, y, z);

    int i, id = 0;
    float dmin = FLT_MAX;
    float dmin2 = FLT_MAX;
    for (i = 0; i < 5; i++)
    {
        float dx = npoints[i][0] - temp;
        float dy = npoints[i][1] - humidity;
        float dsq = dx*dx + dy*dy + npoints[i][2];
        if (dsq < dmin)
        {
            dmin2 = dmin;
            dmin = dsq;
            id = i;
        }
        else if (dsq < dmin2)
            dmin2 = dsq;
    }

    if (ndel)
        *ndel = sqrtf(dmin2) - sqrtf(dmin);

    id = (int) npoints[id][3];
    return id;
}


static void fillRad3D(int *out, int x, int y, int z, int sx, int sy, int sz,
    int id, float rad)
{
    int r, rsq;
    int i, j, k;
    r = (int) (rad);
    if (r <= 0)
        return;
    rsq = (int) floor(rad * rad);

    for (k = -r; k <= r; k++)
    {
        int ak = y+k;
        if (ak < 0 || ak >= sy)
            continue;
        int ksq = k*k;
        int *yout = &out[(int64_t)ak*sx*sz];

        for (j = -r; j <= r; j++)
        {
            int aj = z+j;
            if (aj < 0 || aj >= sz)
                continue;
            int jksq = j*j + ksq;
            for (i = -r; i <= r; i++)
            {
                int ai = x+i;
                if (ai < 0 || ai >= sx)
                    continue;
                int ijksq = i*i + jksq;
                if (ijksq > rsq)
                    continue;

                yout[(int64_t)aj*sx+ai] = id;
            }
        }
    }
}

int mapNether3D(const NetherNoise *nn, int *out, Range r, float confidence)
{
    int64_t i, j, k;
    if (r.sy <= 0)
        r.sy = 1;
    if (r.scale <= 3)
    {
        printf("mapNether3D() invalid scale for this function\n");
        return 1;
    }
    int scale = r.scale / 4;

    memset(out, 0, sizeof(int) * r.sx*r.sy*r.sz);

    // The noisedelta is the distance between the first and second closest
    // biomes within the noise space. Dividing this by the greatest possible
    // gradient (~0.05) gives a minimum diameter of voxels around the sample
    // cell that will have the same biome.
    float invgrad = 1.0 / (confidence * 0.05 * 2) / scale;

    for (k = 0; k < r.sy; k++)
    {
        int *yout = &out[k*r.sx*r.sz];

        for (j = 0; j < r.sz; j++)
        {
            for (i = 0; i < r.sx; i++)
            {
                if (yout[j*r.sx+i])
                    continue;
                //yout[j*w+i] = getNetherBiome(nn, x+i, y+k, z+j, NULL);
                //continue;

                float noisedelta;
                int xi = (r.x+i)*scale;
                int yk = (r.y+k);
                int zj = (r.z+j)*scale;
                int v = getNetherBiome(nn, xi, yk, zj, &noisedelta);
                yout[j*r.sx+i] = v;
                float cellrad = noisedelta * invgrad;
                fillRad3D(out, i, j, k, r.sx, r.sy, r.sz, v, cellrad);
            }
        }
    }
    return 0;
}

int mapNether2D(const NetherNoise *nn, int *out, int x, int z, int w, int h)
{
    Range r = {4, x, z, w, h, 0, 1};
    return mapNether3D(nn, out, r, 1.0);
}

int genNetherScaled(const NetherNoise *nn, int *out, Range r, int mc, uint64_t sha)
{
    if (r.scale <= 0) r.scale = 4;
    if (r.sy == 0) r.sy = 1;

    uint64_t siz = (uint64_t)r.sx*r.sy*r.sz;

    if (mc <= MC_1_15)
    {
        uint64_t i;
        for (i = 0; i < siz; i++)
            out[i] = nether_wastes;
        return 0;
    }

    if (r.scale == 1)
    {
        Range s = getVoronoiSrcRange(r);
        int *src;
        if (siz > 1)
        {   // the source range is large enough that we can try optimizing
            src = out + siz;
            int err = mapNether3D(nn, src, s, 1.0);
            if (err)
                return err;
        }
        else
        {
            src = NULL;
        }

        int i, j, k;
        int *p = out;
        for (k = 0; k < r.sy; k++)
        {
            for (j = 0; j < r.sz; j++)
            {
                for (i = 0; i < r.sx; i++)
                {
                    int x4, z4, y4;
                    voronoiAccess3D(sha, r.x+i, r.y+k, r.z+j, &x4, &y4, &z4);
                    if (src)
                    {
                        x4 -= s.x; y4 -= s.y; z4 -= s.z;
                        *p = src[(int64_t)y4*s.sx*s.sz + (int64_t)z4*s.sx + x4];
                    }
                    else
                    {
                        *p = getNetherBiome(nn, x4, y4, z4, NULL);
                    }
                    p++;
                }
            }
        }
        return 0;
    }
    else
    {
        return mapNether3D(nn, out, r, 1.0);
    }
}


void setEndSeed(EndNoise *en, int mc, uint64_t seed)
{
    uint64_t s;
    setSeed(&s, seed);
    skipNextN(&s, 17292);
    perlinInit(&en->perlin, &s);
    en->mc = mc;
}

static int getEndBiome(int hx, int hz, const uint16_t *hmap, int hw)
{
    int i, j;
    const uint16_t ds[26] = { // (25-2*i)*(25-2*i)
        //  0    1    2    3    4    5    6    7    8    9   10   11   12
          625, 529, 441, 361, 289, 225, 169, 121,  81,  49,  25,   9,   1,
        // 13   14   15   16   17   18   19   20   21   22   23   24,  25
            1,   9,  25,  49,  81, 121, 169, 225, 289, 361, 441, 529, 625,
    };

    const uint16_t *p_dsi = ds + (hx < 0);
    const uint16_t *p_dsj = ds + (hz < 0);
    const uint16_t *p_elev = hmap;
    uint32_t h;

    if (abs(hx) <= 15 && abs(hz) <= 15)
        h = 64 * (hx*hx + hz*hz);
    else
        h = 14401;

    for (j = 0; j < 25; j++)
    {
        uint16_t dsj = p_dsj[j];
        uint16_t e;
        uint32_t u;

        // force unroll for(i=0;i<25;i++) in a cross compatible way
        #define x5(i,x)    { x; i++; x; i++; x; i++; x; i++; x; i++; }
        #define for25(i,x) { i = 0; x5(i,x) x5(i,x) x5(i,x) x5(i,x) x5(i,x) }
        for25(i,
            if unlikely(e = p_elev[i])
            {
                if ((u = (p_dsi[i] + (uint32_t)dsj) * e) < h)
                    h = u;
            }
        );
        #undef for25
        #undef x5
        p_elev += hw;
    }

    if (h < 3600)
        return end_highlands;
    else if (h <= 10000)
        return end_midlands;
    else if (h <= 14400)
        return end_barrens;

    return small_end_islands;
}

int mapEndBiome(const EndNoise *en, int *out, int x, int z, int w, int h)
{
    int64_t i, j;
    int64_t hw = w + 26;
    int64_t hh = h + 26;
    uint16_t *hmap = (uint16_t*) malloc(sizeof(*hmap) * hw * hh);

    for (j = 0; j < hh; j++)
    {
        for (i = 0; i < hw; i++)
        {
            int64_t rx = x + i - 12;
            int64_t rz = z + j - 12;
            uint64_t rsq = rx * rx + rz * rz;
            uint16_t v = 0;
            if (rsq > 4096 && sampleSimplex2D(&en->perlin, rx, rz) < -0.9f)
            {
                //v = (llabs(rx) * 3439 + llabs(rz) * 147) % 13 + 9;
                v = (unsigned int)(
                        fabsf((float)rx) * 3439.0f + fabsf((float)rz) * 147.0f
                    ) % 13 + 9;
                v *= v;
            }
            hmap[(int64_t)j*hw+i] = v;
        }
    }

    for (j = 0; j < h; j++)
    {
        for (i = 0; i < w; i++)
        {
            int64_t hx = (i+x);
            int64_t hz = (j+z);
            uint64_t rsq = hx * hx + hz * hz;

            if (rsq <= 4096L)
                out[j*w+i] = the_end;
            else
            {
                hx = 2*hx + 1;
                hz = 2*hz + 1;
                if (en->mc > MC_1_13)
                {   // add outer end rings
                    rsq = hx * hx + hz * hz;
                    if ((int)rsq < 0)
                    {
                        out[j*w+i] = end_barrens;
                        continue;
                    }
                }
                uint16_t *p_elev = &hmap[(hz/2-z)*hw + (hx/2-x)];
                out[j*w+i] = getEndBiome(hx, hz, p_elev, hw);
            }
        }
    }

    free(hmap);
    return 0;
}

int mapEnd(const EndNoise *en, int *out, int x, int z, int w, int h)
{
    int cx = x >> 2;
    int cz = z >> 2;
    int64_t cw = ((x+w) >> 2) + 1 - cx;
    int64_t ch = ((z+h) >> 2) + 1 - cz;

    int *buf = (int*) malloc(sizeof(int) * cw * ch);
    mapEndBiome(en, buf, cx, cz, cw, ch);

    int i, j;

    for (j = 0; j < h; j++)
    {
        int cj = ((z+j) >> 2) - cz;
        for (i = 0; i < w; i++)
        {
            int ci = ((x+i) >> 2) - cx;
            int v = buf[cj*cw+ci];
            out[j*w+i] = v;
        }
    }

    free(buf);
    return 0;
}

/* Samples the End height. The coordinates used here represent eight blocks per
 * cell. By default a range of 12 cells is sampled, which can be overriden for
 * optimization purposes.
 */
float getEndHeightNoise(const EndNoise *en, int x, int z, int range)
{
    int hx = x / 2;
    int hz = z / 2;
    int oddx = x % 2;
    int oddz = z % 2;
    int i, j;

    int64_t h = 64 * (x*(int64_t)x + z*(int64_t)z);
    if (range == 0)
        range = 12;

    for (j = -range; j <= range; j++)
    {
        for (i = -range; i <= range; i++)
        {
            int64_t rx = hx + i;
            int64_t rz = hz + j;
            uint64_t rsq = rx*rx + rz*rz;
            uint16_t v = 0;
            if (rsq > 4096 && sampleSimplex2D(&en->perlin, rx, rz) < -0.9f)
            {
                //v = (llabs(rx) * 3439 + llabs(rz) * 147) % 13 + 9;
                v = (unsigned int)(
                        fabsf((float)rx) * 3439.0f + fabsf((float)rz) * 147.0f
                    ) % 13 + 9;
                rx = (oddx - i * 2);
                rz = (oddz - j * 2);
                rsq = rx*rx + rz*rz;
                int64_t noise = rsq * v*v;
                if (noise < h)
                    h = noise;
            }
        }
    }

    float ret = 100 - sqrtf((float) h);
    if (ret < -100) ret = -100;
    if (ret > 80) ret = 80;
    return ret;
}

void sampleNoiseColumnEnd(double column[],
    const SurfaceNoise *sn, const EndNoise *en, int x, int z,
    int colymin, int colymax)
{
    // clamped (32 + 46 - y) / 64.0
    static const double upper_drop[] = {
           1.0,    1.0,    1.0,    1.0,    1.0,    1.0,    1.0,    1.0, // 0-7
           1.0,    1.0,    1.0,    1.0,    1.0,    1.0,    1.0, 63./64, // 8-15
        62./64, 61./64, 60./64, 59./64, 58./64, 57./64, 56./64, 55./64, // 16-23
        54./64, 53./64, 52./64, 51./64, 50./64, 49./64, 48./64, 47./64, // 24-31
        46./64 // 32
    };
    // clamped (y - 1) / 7.0
    static const double lower_drop[] = {
          0.0,  0.0, 1./7, 2./7, 3./7, 4./7, 5./7, 6./7, // 0-7
          1.0,  1.0,  1.0,  1.0,  1.0,  1.0,  1.0,  1.0, // 8-15
          1.0,  1.0,  1.0,  1.0,  1.0,  1.0,  1.0,  1.0, // 16-23
          1.0,  1.0,  1.0,  1.0,  1.0,  1.0,  1.0,  1.0, // 24-31
          1.0, // 32
    };

    int y;
    if (en->mc > MC_1_13)
    {   // add outer end rings
        uint64_t rsq = (uint64_t) x * x + (uint64_t) z * z;
        if ((int)rsq < 0)
        {
            for (y = colymin; y <= colymax; y++)
                column[y - colymin] = nan("");
            return;
        }
    }

    // depth is between [-108, +72]
    // noise is between [-128, +128]
    // for a sold block we need the upper drop as:
    //  (72 + 128) * u - 3000 * (1-u) > 0 => upper_drop = u < 15/16
    // which occurs at y = 18 for the highest relevant noise cell
    // for the lower drop we need:
    //  (72 + 128) * l - 30 * (1-l) > 0 => lower_drop = l > 3/23
    // which occurs at y = 3 for the lowest relevant noise cell

    double depth = getEndHeightNoise(en, x, z, 0) - 8.0f;
    for (y = colymin; y <= colymax; y++)
    {
        if (lower_drop[y] == 0.0) {
            column[y - colymin] = -30;
            continue;
        }
        double noise = sampleSurfaceNoiseBetween(sn, x, y, z, -128, +128);
        double clamped = noise + depth;
        clamped = lerp(upper_drop[y], -3000, clamped);
        clamped = lerp(lower_drop[y], -30, clamped);
        column[y - colymin] = clamped;
    }
}

/* Given bordering noise columns and a fractional position between those,
 * determine the surface block height (i.e. where the interpolated noise > 0).
 * Note that the noise columns should be of size: ncolxz[ colymax-colymin+1 ]
 */
int getSurfaceHeight(
        const double ncol00[], const double ncol01[],
        const double ncol10[], const double ncol11[],
        int colymin, int colymax, int blockspercell, double dx, double dz)
{
    int y, celly;
    for (celly = colymax-1; celly >= colymin; celly--)
    {
        int idx = celly - colymin;
        double v000 = ncol00[idx];
        double v001 = ncol01[idx];
        double v100 = ncol10[idx];
        double v101 = ncol11[idx];
        double v010 = ncol00[idx+1];
        double v011 = ncol01[idx+1];
        double v110 = ncol10[idx+1];
        double v111 = ncol11[idx+1];

        for (y = blockspercell - 1; y >= 0; y--)
        {
            double dy = y / (double) blockspercell;
            double noise = lerp3(dy, dx, dz, // Note: not x, y, z
                v000, v010, v100, v110,
                v001, v011, v101, v111);
            if (noise > 0)
                return celly * blockspercell + y;
        }
    }
    return 0;
}

int getEndSurfaceHeight(int mc, uint64_t seed, int x, int z)
{
    EndNoise en;
    setEndSeed(&en, mc, seed);

    SurfaceNoise sn;
    initSurfaceNoise(&sn, DIM_END, seed);

    // end noise columns vary on a grid of cell size = eight
    int cellx = (x >> 3);
    int cellz = (z >> 3);
    double dx = (x & 7) / 8.0;
    double dz = (z & 7) / 8.0;

    // abusing enum for local compile time constants rather than enumeration
    enum { y0 = 0, y1 = 32, yn = y1-y0+1 };
    double ncol00[yn];
    double ncol01[yn];
    double ncol10[yn];
    double ncol11[yn];
    sampleNoiseColumnEnd(ncol00, &sn, &en, cellx, cellz, y0, y1);
    sampleNoiseColumnEnd(ncol01, &sn, &en, cellx, cellz+1, y0, y1);
    sampleNoiseColumnEnd(ncol10, &sn, &en, cellx+1, cellz, y0, y1);
    sampleNoiseColumnEnd(ncol11, &sn, &en, cellx+1, cellz+1, y0, y1);

    return getSurfaceHeight(ncol00, ncol01, ncol10, ncol11, y0, y1, 4, dx, dz);
}

int mapEndSurfaceHeight(float *y, const EndNoise *en, const SurfaceNoise *sn,
    int x, int z, int w, int h, int scale, int ymin)
{
    if (scale != 1 && scale != 2 && scale != 4 && scale != 8)
        return 1;

    int y0 = ymin >> 2;
    if (y0 <  2) y0 =  2;
    if (y0 > 17) y0 = 17;
    int y1 = 18;
    int yn = y1 - y0 + 1;
    double cellmid = scale > 1 ? scale / 16.0 : 0;
    int cellsiz = 8 / scale;
    int cx = floordiv(x, cellsiz);
    int cz = floordiv(z, cellsiz);
    int cw = floordiv(x + w - 1, cellsiz) - cx + 2;
    int i, j;

    double *buf = malloc(sizeof(double) * yn * cw * 2);
    double *ncol[2];
    ncol[0] = buf;
    ncol[1] = buf + yn * cw;

    for (i = 0; i < cw; i++)
        sampleNoiseColumnEnd(ncol[1]+i*yn, sn, en, cx+i, cz+0, y0, y1);

    for (j = 0; j < h; j++)
    {
        int cj = floordiv(z + j, cellsiz);
        int dj = z + j - cj * cellsiz;
        if (j == 0 || dj == 0)
        {
            double *tmp = ncol[0];
            ncol[0] = ncol[1];
            ncol[1] = tmp;
            for (i = 0; i < cw; i++)
                sampleNoiseColumnEnd(ncol[1]+i*yn, sn, en, cx+i, cj+1, y0, y1);
        }

        for (i = 0; i < w; i++)
        {
            int ci = floordiv(x + i, cellsiz);
            int di = x + i - ci * cellsiz;
            double dx = di / (double) cellsiz + cellmid;
            double dz = dj / (double) cellsiz + cellmid;
            double *ncol0 = ncol[0] + (ci - cx) * yn;
            double *ncol1 = ncol[1] + (ci - cx) * yn;
            y[j*w+i] = getSurfaceHeight(ncol0, ncol1, ncol0+yn, ncol1+yn,
                y0, y1, 4, dx, dz);
        }
    }

    free(buf);
    return 0;
}

int genEndScaled(const EndNoise *en, int *out, Range r, int mc, uint64_t sha)
{
    if (mc < MC_1_0)
        return 1;
    if (r.sy == 0)
        r.sy = 1;

    if (mc <= MC_1_8)
    {
        uint64_t i, siz = (uint64_t)r.sx*r.sy*r.sz;
        for (i = 0; i < siz; i++)
            out[i] = the_end;
        return 0;
    }

    int err, iy;

    if (r.scale == 1)
    {
        Range s = getVoronoiSrcRange(r);
        err = mapEnd(en, out, s.x, s.z, s.sx, s.sz);
        if (err) return err;

        if (mc <= MC_1_14)
        {   // up to 1.14 voronoi noise is planar
            Layer lvoronoi;
            memset(&lvoronoi, 0, sizeof(Layer));
            lvoronoi.startSalt = getLayerSalt(10);
            err = mapVoronoi114(&lvoronoi, out, r.x, r.z, r.sx, r.sz);
            if (err) return err;
        }
        else
        {   // in 1.15 voronoi noise varies vertically in the End
            int *src = out + (int64_t)r.sx*r.sy*r.sz;
            memmove(src, out, sizeof(int)*s.sx*s.sz);
            for (iy = 0; iy < r.sy; iy++)
            {
                mapVoronoiPlane(
                    sha, out+r.sx*r.sz*iy, src,
                    r.x,r.z,r.sx,r.sz, r.y+iy,
                    s.x,s.z,s.sx,s.sz);
            }
            return 0; // 3D expansion is done => return
        }
    }
    else if (r.scale == 4)
    {
        err = mapEnd(en, out, r.x, r.z, r.sx, r.sz);
        if (err) return err;
    }
    else if (r.scale == 16)
    {
        err = mapEndBiome(en, out, r.x, r.z, r.sx, r.sz);
        if (err) return err;
    }
    else
    {
        float d = r.scale / 8.0;
        int i, j;
        for (j = 0; j < r.sz; j++)
        {
            for (i = 0; i < r.sx; i++)
            {
                int64_t hx = (int64_t)((i+r.x) * d);
                int64_t hz = (int64_t)((j+r.z) * d);
                uint64_t rsq = hx*hx + hz*hz;
                if (rsq <= 16384L)
                {
                    out[j*r.sx+i] = the_end;
                    continue;
                }
                else if (mc > MC_1_13 && (int)(rsq) < 0)
                {
                    out[j*r.sx+i] = end_barrens;
                    continue;
                }
                float h = getEndHeightNoise(en, hx, hz, 4);
                if (h > 40)
                    out[j*r.sx+i] = end_highlands;
                else if (h >= 0)
                    out[j*r.sx+i] = end_midlands;
                else if (h >= -20)
                    out[j*r.sx+i] = end_barrens;
                else
                    out[j*r.sx+i] = small_end_islands;
            }
        }
    }

    // expanding 2D into 3D
    for (iy = 1; iy < r.sy; iy++)
    {
        int64_t i, siz = (int64_t)r.sx*r.sz;
        for (i = 0; i < siz; i++)
            out[iy*siz + i] = out[i];
    }

    return 0;
}


//==============================================================================
// Overworld and Nether Biome Generation 1.18
//==============================================================================

static int init_climate_seed(
    DoublePerlinNoise *dpn, PerlinNoise *oct,
    uint64_t xlo, uint64_t xhi, int large, int nptype, int nmax
    )
{
    Xoroshiro pxr;
    int n = 0;

    switch (nptype)
    {
    case NP_SHIFT: {
        static const double amp[] = {1, 1, 1, 0};
        // md5 "minecraft:offset"
        pxr.lo = xlo ^ 0x080518cf6af25384;
        pxr.hi = xhi ^ 0x3f3dfb40a54febd5;
        n += xDoublePerlinInit(dpn, &pxr, oct, amp, -3, 4, nmax);
        } break;

    case NP_TEMPERATURE: {
        static const double amp[] = {1.5, 0, 1, 0, 0, 0};
        // md5 "minecraft:temperature" or "minecraft:temperature_large"
        pxr.lo = xlo ^ (large ? 0x944b0073edf549db : 0x5c7e6b29735f0d7f);
        pxr.hi = xhi ^ (large ? 0x4ff44347e9d22b96 : 0xf7d86f1bbc734988);
        n += xDoublePerlinInit(dpn, &pxr, oct, amp, large ? -12 : -10, 6, nmax);
        } break;

    case NP_HUMIDITY: {
        static const double amp[] = {1, 1, 0, 0, 0, 0};
        // md5 "minecraft:vegetation" or "minecraft:vegetation_large"
        pxr.lo = xlo ^ (large ? 0x71b8ab943dbd5301 : 0x81bb4d22e8dc168e);
        pxr.hi = xhi ^ (large ? 0xbb63ddcf39ff7a2b : 0xf1c8b4bea16303cd);
        n += xDoublePerlinInit(dpn, &pxr, oct, amp, large ? -10 : -8, 6, nmax);
        } break;

    case NP_CONTINENTALNESS: {
        static const double amp[] = {1, 1, 2, 2, 2, 1, 1, 1, 1};
        // md5 "minecraft:continentalness" or "minecraft:continentalness_large"
        pxr.lo = xlo ^ (large ? 0x9a3f51a113fce8dc : 0x83886c9d0ae3a662);
        pxr.hi = xhi ^ (large ? 0xee2dbd157e5dcdad : 0xafa638a61b42e8ad);
        n += xDoublePerlinInit(dpn, &pxr, oct, amp, large ? -11 : -9, 9, nmax);
        } break;

    case NP_EROSION: {
        static const double amp[] = {1, 1, 0, 1, 1};
        // md5 "minecraft:erosion" or "minecraft:erosion_large"
        pxr.lo = xlo ^ (large ? 0x8c984b1f8702a951 : 0xd02491e6058f6fd8);
        pxr.hi = xhi ^ (large ? 0xead7b1f92bae535f : 0x4792512c94c17a80);
        n += xDoublePerlinInit(dpn, &pxr, oct, amp, large ? -11 : -9, 5, nmax);
        } break;

    case NP_WEIRDNESS: {
        static const double amp[] = {1, 2, 1, 0, 0, 0};
        // md5 "minecraft:ridge"
        pxr.lo = xlo ^ 0xefc8ef4d36102b34;
        pxr.hi = xhi ^ 0x1beeeb324a0f24ea;
        n += xDoublePerlinInit(dpn, &pxr, oct, amp, -7, 6, nmax);
        } break;

    default:
        printf("unsupported climate parameter %d\n", nptype);
        exit(1);
    }
    return n;
}

void setBiomeSeed(BiomeNoise *bn, uint64_t seed, int large)
{
    Xoroshiro pxr;
    xSetSeed(&pxr, seed);
    uint64_t xlo = xNextLong(&pxr);
    uint64_t xhi = xNextLong(&pxr);

    int n = 0, i = 0;
    for (; i < NP_MAX; i++)
        n += init_climate_seed(&bn->climate[i], bn->oct+n, xlo, xhi, large, i, -1);

    if ((size_t)n > sizeof(bn->oct) / sizeof(*bn->oct))
    {
        printf("setBiomeSeed(): BiomeNoise is malformed, buffer too small\n");
        exit(1);
    }
    bn->nptype = -1;
}

void setBetaBiomeSeed(BiomeNoiseBeta *bnb, uint64_t seed)
{
    uint64_t seedScratch;
    setSeed(&seedScratch, seed*9871);
    octaveInitBeta(bnb->climate, &seedScratch, bnb->oct,
        4, 0.025/1.5, 0.25, 0.55, 2.0);
    setSeed(&seedScratch, seed*39811);
    octaveInitBeta(bnb->climate+1, &seedScratch, bnb->oct+4,
        4, 0.05/1.5, 1./3, 0.55, 2.0);
    setSeed(&seedScratch, seed*0x84a59L);
    octaveInitBeta(bnb->climate+2, &seedScratch, bnb->oct+8,
        2, 0.25/1.5, 10./17, 0.55, 2.0);
    bnb->nptype = -1;
}


enum { SP_CONTINENTALNESS, SP_EROSION, SP_RIDGES, SP_WEIRDNESS };

static void addSplineVal(Spline *rsp, float loc, Spline *val, float der)
{
    rsp->loc[rsp->len] = loc;
    rsp->val[rsp->len] = val;
    rsp->der[rsp->len] = der;
    rsp->len++;
    //if (rsp->len > 12) {
    //    printf("addSplineVal(): too many spline points\n");
    //    exit(1);
    //}
}

static Spline *createFixSpline(SplineStack *ss, float val)
{
    FixSpline *sp = &ss->fstack[ss->flen++];
    sp->len = 1;
    sp->val = val;
    return (Spline*)sp;
}

static float getOffsetValue(float weirdness, float continentalness)
{
    float f0 = 1.0F - (1.0F - continentalness) * 0.5F;
    float f1 = 0.5F * (1.0F - continentalness);
    float f2 = (weirdness + 1.17F) * 0.46082947F;
    float off = f2 * f0 - f1;
    if (weirdness < -0.7F)
        return off > -0.2222F ? off : -0.2222F;
    else
        return off > 0 ? off : 0;
}

static Spline *createSpline_38219(SplineStack *ss, float f, int bl)
{
    Spline *sp = &ss->stack[ss->len++];
    sp->typ = SP_RIDGES;

    float i = getOffsetValue(-1.0F, f);
    float k = getOffsetValue( 1.0F, f);
    float l = 1.0F - (1.0F - f) * 0.5F;
    float u = 0.5F * (1.0F - f);
    l = u / (0.46082947F * l) - 1.17F;

    if (-0.65F < l && l < 1.0F)
    {
        float p, q, r, s;
        u = getOffsetValue(-0.65F, f);
        p = getOffsetValue(-0.75F, f);
        q = (p - i) * 4.0F;
        r = getOffsetValue(l, f);
        s = (k - r) / (1.0F - l);

        addSplineVal(sp, -1.0F,     createFixSpline(ss, i), q);
        addSplineVal(sp, -0.75F,    createFixSpline(ss, p), 0);
        addSplineVal(sp, -0.65F,    createFixSpline(ss, u), 0);
        addSplineVal(sp, l-0.01F,   createFixSpline(ss, r), 0);
        addSplineVal(sp, l,         createFixSpline(ss, r), s);
        addSplineVal(sp, 1.0F,      createFixSpline(ss, k), s);
    }
    else
    {
        u = (k - i) * 0.5F;
        if (bl) {
            addSplineVal(sp, -1.0F, createFixSpline(ss, i > 0.2 ? i : 0.2), 0);
            addSplineVal(sp,  0.0F, createFixSpline(ss, lerp(0.5F, i, k)), u);
        } else {
            addSplineVal(sp, -1.0F, createFixSpline(ss, i), u);
        }
        addSplineVal(sp, 1.0F,      createFixSpline(ss, k), u);
    }
    return sp;
}

static Spline *createFlatOffsetSpline(
    SplineStack *ss, float f, float g, float h, float i, float j, float k)
{
    Spline *sp = &ss->stack[ss->len++];
    sp->typ = SP_RIDGES;

    float l = 0.5F * (g - f); if (l < k) l = k;
    float m = 5.0F * (h - g);

    addSplineVal(sp, -1.0F, createFixSpline(ss, f), l);
    addSplineVal(sp, -0.4F, createFixSpline(ss, g), l < m ? l : m);
    addSplineVal(sp,  0.0F, createFixSpline(ss, h), m);
    addSplineVal(sp,  0.4F, createFixSpline(ss, i), 2.0F*(i-h));
    addSplineVal(sp,  1.0F, createFixSpline(ss, j), 0.7F*(j-i));

    return sp;
}

static Spline *createLandSpline(
    SplineStack *ss, float f, float g, float h, float i, float j, float k, int bl)
{
    Spline *sp1 = createSpline_38219(ss, lerp(i, 0.6F, 1.5F), bl);
    Spline *sp2 = createSpline_38219(ss, lerp(i, 0.6F, 1.0F), bl);
    Spline *sp3 = createSpline_38219(ss, i, bl);
    const float ih = 0.5F * i;
    Spline *sp4 = createFlatOffsetSpline(ss, f-0.15F, ih, ih, ih, i*0.6F, 0.5F);
    Spline *sp5 = createFlatOffsetSpline(ss, f, j*i, g*i, ih, i*0.6F, 0.5F);
    Spline *sp6 = createFlatOffsetSpline(ss, f, j, j, g, h, 0.5F);
    Spline *sp7 = createFlatOffsetSpline(ss, f, j, j, g, h, 0.5F);

    Spline *sp8 = &ss->stack[ss->len++];
    sp8->typ = SP_RIDGES;
    addSplineVal(sp8, -1.0F, createFixSpline(ss, f), 0.0F);
    addSplineVal(sp8, -0.4F, sp6, 0.0F);
    addSplineVal(sp8,  0.0F, createFixSpline(ss, h + 0.07F), 0.0F);

    Spline *sp9 = createFlatOffsetSpline(ss, -0.02F, k, k, g, h, 0.0F);
    Spline *sp = &ss->stack[ss->len++];
    sp->typ = SP_EROSION;
    addSplineVal(sp, -0.85F, sp1, 0.0F);
    addSplineVal(sp, -0.7F,  sp2, 0.0F);
    addSplineVal(sp, -0.4F,  sp3, 0.0F);
    addSplineVal(sp, -0.35F, sp4, 0.0F);
    addSplineVal(sp, -0.1F,  sp5, 0.0F);
    addSplineVal(sp,  0.2F,  sp6, 0.0F);
    if (bl) {
        addSplineVal(sp, 0.4F,  sp7, 0.0F);
        addSplineVal(sp, 0.45F, sp8, 0.0F);
        addSplineVal(sp, 0.55F, sp8, 0.0F);
        addSplineVal(sp, 0.58F, sp7, 0.0F);
    }
    addSplineVal(sp, 0.7F, sp9, 0.0F);
    return sp;
}

float getSpline(const Spline *sp, const float *vals)
{
    if (!sp || sp->len <= 0 || sp->len >= 12)
    {
        printf("getSpline(): bad parameters\n");
        exit(1);
    }

    if (sp->len == 1)
        return ((FixSpline*)sp)->val;

    float f = vals[sp->typ];
    int i;

    for (i = 0; i < sp->len; i++)
        if (sp->loc[i] >= f)
            break;
    if (i == 0 || i == sp->len)
    {
        if (i) i--;
        float v = getSpline(sp->val[i], vals);
        return v + sp->der[i] * (f - sp->loc[i]);
    }
    const Spline *sp1 = sp->val[i-1];
    const Spline *sp2 = sp->val[i];
    float g = sp->loc[i-1];
    float h = sp->loc[i];
    float k = (f - g) / (h - g);
    float l = sp->der[i-1];
    float m = sp->der[i];
    float n = getSpline(sp1, vals);
    float o = getSpline(sp2, vals);
    float p = l * (h - g) - (o - n);
    float q = -m * (h - g) + (o - n);
    float r = lerp(k, n, o) + k * (1.0F - k) * lerp(k, p, q);
    return r;
}

void initBiomeNoise(BiomeNoise *bn, int mc)
{
    SplineStack *ss = &bn->ss;
    memset(ss, 0, sizeof(*ss));
    Spline *sp = &ss->stack[ss->len++];
    sp->typ = SP_CONTINENTALNESS;

    Spline *sp1 = createLandSpline(ss, -0.15F, 0.00F, 0.0F, 0.1F, 0.00F, -0.03F, 0);
    Spline *sp2 = createLandSpline(ss, -0.10F, 0.03F, 0.1F, 0.1F, 0.01F, -0.03F, 0);
    Spline *sp3 = createLandSpline(ss, -0.10F, 0.03F, 0.1F, 0.7F, 0.01F, -0.03F, 1);
    Spline *sp4 = createLandSpline(ss, -0.05F, 0.03F, 0.1F, 1.0F, 0.01F,  0.01F, 1);

    addSplineVal(sp, -1.10F, createFixSpline(ss,  0.044F), 0.0F);
    addSplineVal(sp, -1.02F, createFixSpline(ss, -0.2222F), 0.0F);
    addSplineVal(sp, -0.51F, createFixSpline(ss, -0.2222F), 0.0F);
    addSplineVal(sp, -0.44F, createFixSpline(ss, -0.12F), 0.0F);
    addSplineVal(sp, -0.18F, createFixSpline(ss, -0.12F), 0.0F);
    addSplineVal(sp, -0.16F, sp1, 0.0F);
    addSplineVal(sp, -0.15F, sp1, 0.0F);
    addSplineVal(sp, -0.10F, sp2, 0.0F);
    addSplineVal(sp,  0.25F, sp3, 0.0F);
    addSplineVal(sp,  1.00F, sp4, 0.0F);

    bn->sp = sp;
    bn->mc = mc;
}


/// Biome sampler for MC 1.18
int sampleBiomeNoise(const BiomeNoise *bn, int64_t *np, int x, int y, int z,
    uint64_t *dat, uint32_t sample_flags)
{
    if (bn->nptype >= 0)
    {   // initialized for a specific climate parameter
        if (np)
            memset(np, 0, NP_MAX*sizeof(*np));
        int64_t id = (int64_t) (10000.0 * sampleClimatePara(bn, np, x, z));
        return (int) id;
    }

    float t = 0, h = 0, c = 0, e = 0, d = 0, w = 0;
    double px = x, pz = z;
    if (!(sample_flags & SAMPLE_NO_SHIFT))
    {
        px += sampleDoublePerlin(&bn->climate[NP_SHIFT], x, 0, z) * 4.0;
        pz += sampleDoublePerlin(&bn->climate[NP_SHIFT], z, x, 0) * 4.0;
    }

    c = sampleDoublePerlin(&bn->climate[NP_CONTINENTALNESS], px, 0, pz);
    e = sampleDoublePerlin(&bn->climate[NP_EROSION], px, 0, pz);
    w = sampleDoublePerlin(&bn->climate[NP_WEIRDNESS], px, 0, pz);

    if (!(sample_flags & SAMPLE_NO_DEPTH))
    {
        float np_param[] = {
            c, e, -3.0F * ( fabsf( fabsf(w) - 0.6666667F ) - 0.33333334F ), w,
        };
        double off = getSpline(bn->sp, np_param) + 0.015F;

        //double py = y + sampleDoublePerlin(&bn->shift, y, z, x) * 4.0;
        d = 1.0 - (y * 4) / 128.0 - 83.0/160.0 + off;
    }

    t = sampleDoublePerlin(&bn->climate[NP_TEMPERATURE], px, 0, pz);
    h = sampleDoublePerlin(&bn->climate[NP_HUMIDITY], px, 0, pz);

    int64_t l_np[6];
    int64_t *p_np = np ? np : l_np;
    p_np[0] = (int64_t)(10000.0F*t);
    p_np[1] = (int64_t)(10000.0F*h);
    p_np[2] = (int64_t)(10000.0F*c);
    p_np[3] = (int64_t)(10000.0F*e);
    p_np[4] = (int64_t)(10000.0F*d);
    p_np[5] = (int64_t)(10000.0F*w);

    int id = none;
    if (!(sample_flags & SAMPLE_NO_BIOME))
        id = climateToBiome(bn->mc, (const uint64_t*)p_np, dat);
    return id;
}

static int climateToBiomeHint(int mc, const uint64_t np[6], int *hint);
/* SSA (p95): sampleBiomeNoise(bn, NULL, x, y, z, NULL, 0) with a leaf hint
 * (see climateToBiomeHint): same result, for grids of neighbouring cells. */
#define SDPH(dpn, x, y, z) (scat ? sampleDoublePerlinScattered(dpn, x, y, z) : sampleDoublePerlin(dpn, x, y, z))
static int sampleBiomeNoiseHint(const BiomeNoise *bn, int x, int y, int z, int *hint,
    uint64_t *dat, uint32_t sample_flags)
{
    int64_t *np = NULL;
    const int scat = (sample_flags & SAMPLE_SSA_SCATTERED) != 0;
    if (bn->nptype >= 0)
    {   // initialized for a specific climate parameter
        if (np)
            memset(np, 0, NP_MAX*sizeof(*np));
        int64_t id = (int64_t) (10000.0 * sampleClimatePara(bn, np, x, z));
        return (int) id;
    }

    float t = 0, h = 0, c = 0, e = 0, d = 0, w = 0;
    double px = x, pz = z;
    if (!(sample_flags & SAMPLE_NO_SHIFT))
    {
        px += SDPH(&bn->climate[NP_SHIFT], x, 0, z) * 4.0;
        pz += SDPH(&bn->climate[NP_SHIFT], z, x, 0) * 4.0;
    }

    c = SDPH(&bn->climate[NP_CONTINENTALNESS], px, 0, pz);
    e = SDPH(&bn->climate[NP_EROSION], px, 0, pz);
    w = SDPH(&bn->climate[NP_WEIRDNESS], px, 0, pz);

    if (!(sample_flags & SAMPLE_NO_DEPTH))
    {
        float np_param[] = {
            c, e, -3.0F * ( fabsf( fabsf(w) - 0.6666667F ) - 0.33333334F ), w,
        };
        double off = getSpline(bn->sp, np_param) + 0.015F;

        //double py = y + SDPH(&bn->shift, y, z, x) * 4.0;
        d = 1.0 - (y * 4) / 128.0 - 83.0/160.0 + off;
    }

    t = SDPH(&bn->climate[NP_TEMPERATURE], px, 0, pz);
    h = SDPH(&bn->climate[NP_HUMIDITY], px, 0, pz);

    int64_t l_np[6];
    int64_t *p_np = np ? np : l_np;
    p_np[0] = (int64_t)(10000.0F*t);
    p_np[1] = (int64_t)(10000.0F*h);
    p_np[2] = (int64_t)(10000.0F*c);
    p_np[3] = (int64_t)(10000.0F*e);
    p_np[4] = (int64_t)(10000.0F*d);
    p_np[5] = (int64_t)(10000.0F*w);

    int id = none;
    if (!(sample_flags & SAMPLE_NO_BIOME))
        id = dat ? climateToBiome(bn->mc, (const uint64_t*)p_np, dat)
                 : climateToBiomeHint(bn->mc, (const uint64_t*)p_np, hint);
    return id;
}
#undef SDPH

// Note: Climate noise is sampled at a 1:1 scale.
int sampleBiomeNoiseBeta(const BiomeNoiseBeta *bnb, int64_t *np, double *nv,
    int x, int z)
{
    if (bnb->nptype >= 0 && np)
        memset(np, 0, 2*sizeof(*np));
    double t, h, f;
    f = sampleOctaveBeta17Biome(&bnb->climate[2], x, z) * 1.1 + 0.5;

    t = (sampleOctaveBeta17Biome(&bnb->climate[0], x, z) *
        0.15 + 0.7) * 0.99 + f * 0.01;
    t = 1 - (1 - t) * (1 - t);
    t = (t < 0) ? 0 : t;
    t = (t > 1) ? 1 : t;
    if (bnb->nptype == NP_TEMPERATURE)
        return (int64_t) (10000.0F * t);

    h = (sampleOctaveBeta17Biome(&bnb->climate[1], x, z) *
        0.15 + 0.5) * 0.998 + f * 0.002;
    h = (h < 0) ? 0 : h;
    h = (h > 1) ? 1 : h;
    if (bnb->nptype == NP_HUMIDITY)
        return (int64_t) (10000.0F * h * t);

    if (nv)
    {
        nv[0] = t;
        nv[1] = h;
    }
    return getOldBetaBiome((float) t, (float) h);
}


int getOldBetaBiome(float t, float h)
{
    static const uint8_t biome_table_beta_1_7[64*64] = 
    {
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,1,1,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,1,1,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,9,9,9,9,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,9,9,9,9,9,9,9,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,6,6,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,6,6,6,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,6,6,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,6,6,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,6,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,6,6,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,6,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,2,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        6,6,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,
        9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,
        9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,
        9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,
        9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,0,0,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,
        9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,
        9,9,9,9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,
        9,9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,
        9,9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,
        9,9,9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,
        9,9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,
        9,9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,
        9,9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,
        9,9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,
        9,9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,
        9,9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,
        9,9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,
        9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,
        9,9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,
        9,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,2,2,2,2,2,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,2,2,2,2,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,2,2,2,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,2,2,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,2,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,2,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,2,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,2,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,2,4,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,7,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,4,4,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,7,8,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,2,4,4,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,8,8,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,4,4,4,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,8,8,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,2,4,4,4,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,8,8,
        5,5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,4,4,4,4,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,8,8,
        5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        2,4,4,4,4,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,8,8,
        5,5,5,5,5,5,5,5,5,5,5,5,5,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,3,
        4,4,4,4,4,4,4,4,4,4,4,4,4,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,2,8,8,
    };
    static const int bmap[] = {
        plains, desert, forest, taiga, swamp, snowy_tundra, savanna,
        seasonal_forest, rainforest, shrubland
    };
    int idx = (int)(t * 63) + (int)(h * 63) * 64;
    return bmap[ biome_table_beta_1_7[idx] ];
}


static
uint64_t get_np_dist(const uint64_t np[6], const BiomeTree *bt, int idx)
{
    uint64_t ds = 0, node = bt->nodes[idx];
    uint64_t a, b, d;
    uint32_t i;

    for (i = 0; i < 6; i++)
    {
        idx = (node >> 8*i) & 0xFF;
        a = np[i] - bt->param[2*idx + 1];
        b = bt->param[2*idx + 0] - np[i];
        d = (int64_t)a > 0 ? a : (int64_t)b > 0 ? b : 0;
        d = d * d;
        ds += d;
    }
    return ds;
}

static
int get_resulting_node(const uint64_t np[6], const BiomeTree *bt, int idx,
    int alt, uint64_t ds, int depth)
{
    if (bt->steps[depth] == 0)
        return idx;
    uint32_t step;
    do
    {
        step = bt->steps[depth];
        depth++;
    }
    while (idx+step >= bt->len);

    uint64_t node = bt->nodes[idx];
    uint16_t inner = node >> 48;

    int leaf = alt;
    uint32_t i, n;

    for (i = 0, n = bt->order; i < n; i++)
    {
        uint64_t ds_inner = get_np_dist(np, bt, inner);
        if (ds_inner < ds)
        {
            int leaf2 = get_resulting_node(np, bt, inner, leaf, ds, depth);
            uint64_t ds_leaf2;
            if (inner == leaf2)
                ds_leaf2 = ds_inner;
            else
                ds_leaf2 = get_np_dist(np, bt, leaf2);
            if (ds_leaf2 < ds)
            {
                ds = ds_leaf2;
                leaf = leaf2;
            }
        }

        inner += step;
        if (inner >= bt->len)
            break;
    }

    return leaf;
}

/* SSA (p92): exact per-leaf climate limits, see biomenoise.h */
static const BiomeTree *ssa_btree(int mc);

/* decoded leaf boxes of a tree, built once per table */
typedef struct { int n; int32_t (*lo)[6], (*hi)[6]; uint8_t *id; int32_t *slo[6], *shi[6]; } LeafBoxes;
static const LeafBoxes *g_leafbox[64];

static const LeafBoxes *leaf_boxes(int mc, const BiomeTree *bt)
{   // called with g_bli_lock held
    if (g_leafbox[mc])
        return g_leafbox[mc];
    LeafBoxes *lb = (LeafBoxes*) calloc(1, sizeof(*lb));
    lb->lo = (int32_t (*)[6]) malloc(bt->len * sizeof(*lb->lo));
    lb->hi = (int32_t (*)[6]) malloc(bt->len * sizeof(*lb->hi));
    lb->id = (uint8_t*) malloc(bt->len);
    for (uint32_t i = 0; i < bt->len; i++)
    {
        uint64_t ni = bt->nodes[i];
        if ((ni >> 56) != 0xFF)
            continue;
        for (int k = 0; k < 6; k++)
        {
            int idx = (ni >> 8*k) & 0xFF;
            lb->lo[lb->n][k] = bt->param[2*idx + 0];
            lb->hi[lb->n][k] = bt->param[2*idx + 1];
        }
        lb->id[lb->n++] = (ni >> 48) & 0xFF;
    }
    for (int k = 0; k < 6; k++)
    {
        lb->slo[k] = (int32_t*) malloc((lb->n + 8) * sizeof(int32_t));
        lb->shi[k] = (int32_t*) malloc((lb->n + 8) * sizeof(int32_t));
        for (int i = 0; i < lb->n; i++)
        {
            lb->slo[k][i] = lb->lo[i][k];
            lb->shi[k][i] = lb->hi[i][k];
        }
    }
    g_leafbox[mc] = lb;
    return lb;
}

static int cmp_iv(const void *a, const void *b)
{
    int32_t x = ((const int32_t*)a)[0], y = ((const int32_t*)b)[0];
    return (x > y) - (x < y);
}

/* does the union of the n intervals cover [A, B] ? (sorts tmp) */
static int covers(const int32_t (*iv)[2], int n, int32_t A, int32_t B, int32_t (*tmp)[2])
{
    if (n <= 0)
        return 0;
    memcpy(tmp, iv, (size_t)n * sizeof(*tmp));
    qsort(tmp, n, sizeof(*tmp), cmp_iv);
    int64_t reach = (int64_t)A - 1;
    for (int q = 0; q < n && reach < B; q++)
    {
        if (tmp[q][0] > reach + 1)
            break;
        if (tmp[q][1] > reach)
            reach = tmp[q][1];
    }
    return reach >= B;
}

typedef struct { int64_t t; int j; } ThrEnt;
static int cmp_thr_up(const void *a, const void *b)
{
    int64_t x = ((const ThrEnt*)a)->t, y = ((const ThrEnt*)b)->t;
    return (x > y) - (x < y);
}
static int cmp_thr_dn(const void *a, const void *b)
{
    return cmp_thr_up(b, a);
}

/* Allowed interval of every parameter for each leaf of 'biome'.
 * Leaf j "beats" leaf i at the value v of parameter k when it is strictly
 * closer to v in k and at least as close as i in every other parameter, for
 * any value of these. With j's interval containing i's in a parameter, j is
 * at least as close there for any value; in ONE other parameter m this may be
 * replaced by a family of leaves whose m-intervals together cover i's (for
 * any value of m one of them is at least as close). i is then never the
 * nearest leaf: v is outside i's allowed interval for k. */
static int leaf_limits(const LeafBoxes *lb, int biome, int (*out)[12], int max)
{
    int n = 0;
    int cap = 256;
    int *cand = (int*) malloc(cap * sizeof(int));
    uint8_t *cmiss = (uint8_t*) malloc(cap);
    ThrEnt *te = (ThrEnt*) malloc(cap * sizeof(ThrEnt));
    int32_t (*tmpiv)[2] = (int32_t (*)[2]) malloc(cap * sizeof(*tmpiv));
    int32_t (*iv)[2] = (int32_t (*)[2]) malloc(cap * sizeof(*iv));
    uint8_t *mbits = (uint8_t*) malloc(lb->n + 8);
    for (int i = 0; i < lb->n; i++)
    {
        if (lb->id[i] != biome)
            continue;
        if (n >= max)
        {
            n = -1;
            break;
        }
        const int32_t *lo = lb->lo[i], *hi = lb->hi[i];
        // leaves that contain i in at least 4 of the 6 parameters
        // (miss bit q: j's interval does not contain i's in parameter q)
        for (int j = 0; j < lb->n; j++)
            mbits[j] = 0;
        for (int q = 0; q < 6; q++)
        {
            const int32_t *sl = lb->slo[q], *sh = lb->shi[q];
            int32_t a = lo[q], b = hi[q];
            uint8_t bit = 1 << q;
            for (int j = 0; j < lb->n; j++)     // vectorizes
                mbits[j] |= (sl[j] > a || sh[j] < b) ? bit : 0;
        }
        int nc = 0;
        for (int j = 0; j < lb->n; j++)
        {
            int miss = mbits[j];
            if (j == i || __builtin_popcount(miss) > 2)
                continue;
            if (nc == cap)
            {
                cap *= 2;
                cand = (int*) realloc(cand, cap * sizeof(int));
                cmiss = (uint8_t*) realloc(cmiss, cap);
                te = (ThrEnt*) realloc(te, cap * sizeof(ThrEnt));
                tmpiv = (int32_t (*)[2]) realloc(tmpiv, cap * sizeof(*tmpiv));
                iv = (int32_t (*)[2]) realloc(iv, cap * sizeof(*iv));
            }
            cand[nc] = j;
            cmiss[nc] = miss;
            nc++;
        }
        for (int k = 0; k < 6; k++)
        {
            int64_t L = INT32_MIN, H = INT32_MAX;
            for (int m = 0; m < 6; m++)
            {
                if (m == k)
                    continue;
                int allow = (1 << k) | (1 << m);
                for (int side = 0; side < 2; side++)
                {
                    // members of the family and the value from which each
                    // one is strictly closer in k (above: v >= t, below: v <= t)
                    int nf = 0;
                    for (int c = 0; c < nc; c++)
                    {
                        if (cmiss[c] & ~allow)
                            continue;
                        int j = cand[c];
                        int32_t jlo = lb->lo[j][k], jhi = lb->hi[j][k];
                        int64_t t;
                        if (side == 0)
                        {
                            if (jhi <= hi[k]) continue;
                            t = jlo <= hi[k] ? (int64_t)hi[k] + 1
                              : (((int64_t)jlo + hi[k]) >> 1) + 1;
                            if (t > H) continue;        // cannot improve
                        }
                        else
                        {
                            if (jlo >= lo[k]) continue;
                            int64_t sum = (int64_t)jhi + lo[k];
                            t = jhi >= lo[k] ? (int64_t)lo[k] - 1
                              : ((sum >> 1) + (sum & 1)) - 1;
                            if (t < L) continue;
                        }
                        te[nf].t = t;
                        te[nf].j = j;
                        nf++;
                    }
                    if (nf == 0)
                        continue;
                    qsort(te, nf, sizeof(*te), side == 0 ? cmp_thr_up : cmp_thr_dn);
                    for (int x = 0; x < nf; x++)
                    {
                        iv[x][0] = lb->lo[te[x].j][m];
                        iv[x][1] = lb->hi[te[x].j][m];
                    }
                    if (!covers((const int32_t (*)[2]) iv, nf, lo[m], hi[m], tmpiv))
                        continue;
                    // smallest prefix (nearest thresholds) that covers
                    int lo_n = 1, hi_n = nf;
                    while (lo_n < hi_n)
                    {
                        int mid = (lo_n + hi_n) / 2;
                        if (covers((const int32_t (*)[2]) iv, mid, lo[m], hi[m], tmpiv))
                            hi_n = mid;
                        else
                            lo_n = mid + 1;
                    }
                    int64_t t = te[lo_n - 1].t;
                    if (side == 0) { if (t - 1 < H) H = t - 1; }
                    else           { if (t + 1 > L) L = t + 1; }
                }
            }
            out[n][2*k+0] = (int) L;
            out[n][2*k+1] = (int) H;
        }
        n++;
    }
    free(cand); free(cmiss); free(te); free(tmpiv); free(iv); free(mbits);
    return n;
}

static volatile int g_bli_lock;

int getBiomeLeafLimits(int mc, int biome, int (*out)[12], int max)
{
    const BiomeTree *bt = ssa_btree(mc);
    if (!bt || mc < 0 || mc >= 64)
        return -1;
    while (__atomic_exchange_n(&g_bli_lock, 1, __ATOMIC_ACQUIRE))
        ;
    const LeafBoxes *lb = leaf_boxes(mc, bt);
    __atomic_store_n(&g_bli_lock, 0, __ATOMIC_RELEASE);
    return leaf_limits(lb, biome, out, max);
}

/* per-biome reduced leaf data, computed once and shared by all threads */
static const BiomeLeafInfo *g_bli[64][256];

static int rows_sub(const int *a, const int *b, int n)
{   // interval row a contained in row b
    for (int k = 0; k < n; k += 2)
        if (a[k] < b[k] || a[k+1] > b[k+1])
            return 0;
    return 1;
}

static int reduce_rows(int *rows, int n, int w)
{   // drop rows contained in another row (keep one of equal rows)
    int m = 0;
    for (int i = 0; i < n; i++)
    {
        int *ri = rows + i*w, drop = 0;
        for (int j = 0; j < n && !drop; j++)
        {
            if (j == i) continue;
            int *rj = rows + j*w;
            if (rows_sub(ri, rj, w) && (!rows_sub(rj, ri, w) || j < i))
                drop = 1;
        }
        if (!drop)
            memmove(rows + m*w, ri, w*sizeof(int)), m++;
    }
    return m;
}

/* The pruning relies on the tree search returning a nearest leaf, which holds
 * when every node box (below the root) contains the boxes of its children.
 * True for the 1.18 - 26.3 tables except 26.2, checked here once per table. */
static int tree_bounded(const BiomeTree *bt, int idx, int depth)
{
    if (bt->steps[depth] == 0)
        return 1;
    uint32_t step;
    do
    {
        step = bt->steps[depth];
        depth++;
    }
    while (idx+step >= bt->len);
    uint64_t node = bt->nodes[idx];
    uint16_t inner = node >> 48;
    for (uint32_t i = 0; i < bt->order; i++)
    {
        uint64_t c = bt->nodes[inner];
        if (idx != 0)
        {
            for (int k = 0; k < 6; k++)
            {
                int pi = (node >> 8*k) & 0xFF, ci = (c >> 8*k) & 0xFF;
                if (bt->param[2*ci] < bt->param[2*pi] ||
                    bt->param[2*ci+1] > bt->param[2*pi+1])
                    return 0;
            }
        }
        if ((c >> 56) != 0xFF && !tree_bounded(bt, inner, depth))
            return 0;
        inner += step;
        if (inner >= bt->len)
            break;
    }
    return 1;
}

const BiomeLeafInfo *getBiomeLeafInfo(int mc, int biome)
{
    if (mc < MC_1_18 || mc >= 64 || biome < 0 || biome >= 256)
        return NULL;
    const BiomeLeafInfo *p = __atomic_load_n(&g_bli[mc][biome], __ATOMIC_ACQUIRE);
    if (p)
        return p;
    while (__atomic_exchange_n(&g_bli_lock, 1, __ATOMIC_ACQUIRE))
        ;
    p = g_bli[mc][biome];
    static signed char exact[64];   // 0 unknown, 1 exact, -1 not
    if (!exact[mc])
        exact[mc] = (ssa_btree(mc) && tree_bounded(ssa_btree(mc), 0, 0)) ? 1 : -1;
    if (!p && exact[mc] > 0)
    {
        int max = 4096;
        int (*lf)[12] = (int (*)[12]) malloc(max * sizeof(*lf));
        int n = leaf_limits(leaf_boxes(mc, ssa_btree(mc)), biome, lf, max);
        if (n >= 0)
        {
            BiomeLeafInfo *bi = (BiomeLeafInfo*) calloc(1, sizeof(*bi));
            int (*th)[4] = (int (*)[4]) malloc((n ? n : 1) * sizeof(*th));
            for (int k = 0; k < 6; k++)
            {
                bi->hull[2*k] = INT_MAX; bi->hull[2*k+1] = INT_MIN;
            }
            for (int i = 0; i < n; i++)
            {
                for (int k = 0; k < 6; k++)
                {
                    if (lf[i][2*k]   < bi->hull[2*k])   bi->hull[2*k]   = lf[i][2*k];
                    if (lf[i][2*k+1] > bi->hull[2*k+1]) bi->hull[2*k+1] = lf[i][2*k+1];
                }
                th[i][0] = lf[i][0]; th[i][1] = lf[i][1];
                th[i][2] = lf[i][2]; th[i][3] = lf[i][3];
            }
            bi->nth = reduce_rows(&th[0][0], n, 4);
            bi->nlf = reduce_rows(&lf[0][0], n, 12);
            lf = (int (*)[12]) realloc(lf, (bi->nlf ? bi->nlf : 1) * sizeof(*lf));
            bi->th = (const int (*)[4]) th;
            bi->lf = (const int (*)[12]) lf;
            lf = NULL;
            p = bi;
            __atomic_store_n(&g_bli[mc][biome], p, __ATOMIC_RELEASE);
        }
        free(lf);
    }
    __atomic_store_n(&g_bli_lock, 0, __ATOMIC_RELEASE);
    return p;
}

int setupBiomePrune(BiomePrune *bp, int mc, const int *ids, int n)
{
    memset(bp, 0, sizeof(*bp));
    if (n <= 0 || n > BPRUNE_MAX)
        return 0;
    for (int i = 0; i < n; i++)
    {
        const BiomeLeafInfo *bi = getBiomeLeafInfo(mc, ids[i]);
        if (!bi)
            return 0;
        bp->b[i] = bi;
    }
    bp->n = n;
    // sampling order: most pruning per octave first. Pruning ~ share of the
    // [-1, 1] range outside the union of the candidates' hulls.
    static const int para[5] = { 0, 1, 5, 3, 2 };     // T, H, W, E, C
    static const double cost[5] = { 2, 2, 3, 4, 6 };  // octaves
    double score[5];
    for (int q = 0; q < 5; q++)
    {
        int k = para[q];
        // union length of the hull intervals clipped to [-10000, 10000]
        int lo[BPRUNE_MAX], hi[BPRUNE_MAX], m = 0;
        for (int i = 0; i < n; i++)
        {
            int a = bp->b[i]->hull[2*k], b = bp->b[i]->hull[2*k+1];
            if (a < -10000) a = -10000;
            if (b > 10000) b = 10000;
            if (a <= b) { lo[m] = a; hi[m] = b; m++; }
        }
        for (int i = 1; i < m; i++)         // sort by lo
            for (int j = i; j > 0 && lo[j] < lo[j-1]; j--)
            {
                int t = lo[j]; lo[j] = lo[j-1]; lo[j-1] = t;
                t = hi[j]; hi[j] = hi[j-1]; hi[j-1] = t;
            }
        double cov = 0; int end = -10001;
        for (int i = 0; i < m; i++)
        {
            int a = lo[i] > end ? lo[i] : end;
            if (hi[i] > a) cov += hi[i] - a;
            if (hi[i] > end) end = hi[i];
        }
        score[q] = (1.0 - cov / 20000.0) / cost[q];
        bp->order[q] = k;
    }
    for (int i = 1; i < 5; i++)             // stable sort, best score first
        for (int j = i; j > 0 && score[j] > score[j-1] + 1e-9; j--)
        {
            double t = score[j]; score[j] = score[j-1]; score[j-1] = t;
            int o = bp->order[j]; bp->order[j] = bp->order[j-1]; bp->order[j-1] = o;
        }
    return 1;
}

static inline uint32_t hull_keep(const BiomePrune *bp, uint32_t alive, int k, int64_t v)
{
    uint32_t a = alive;
    while (a)
    {
        int i = __builtin_ctz(a);
        a &= a - 1;
        const int *h = bp->b[i]->hull;
        if (v < h[2*k] || v > h[2*k+1])
            alive &= ~(1U << i);
    }
    return alive;
}

/* SSA (p92): see biomenoise.h. Same arithmetic as sampleBiomeNoise(), with the
 * climate parameters computed cheapest first and checked after each one. */
#define SDP(dpn, x, y, z) (scat ? sampleDoublePerlinScattered(dpn, x, y, z) : sampleDoublePerlin(dpn, x, y, z))
int sampleBiomeNoisePruned(const BiomeNoise *bn, int x, int y, int z,
    uint64_t *dat, uint32_t sample_flags, const BiomePrune *bp, uint32_t alive)
{
    float t = 0, h = 0, c = 0, e = 0, d = 0, w = 0;
    int64_t p_np[6];
    double px = x, pz = z;
    uint32_t a;
    const int scat = (sample_flags & SAMPLE_SSA_SCATTERED) != 0;
    int i, j, th = 0;
    if (!(sample_flags & SAMPLE_NO_SHIFT))
    {
        px += SDP(&bn->climate[NP_SHIFT], x, 0, z) * 4.0;
        pz += SDP(&bn->climate[NP_SHIFT], z, x, 0) * 4.0;
    }
    // parameters in the prepared order (most selective per octave first)
    for (int q = 0; q < 5; q++)
    {
        int k = bp->order[q];
        switch (k)
        {
        case 0:
            t = SDP(&bn->climate[NP_TEMPERATURE], px, 0, pz);
            p_np[0] = (int64_t)(10000.0F*t);
            break;
        case 1:
            h = SDP(&bn->climate[NP_HUMIDITY], px, 0, pz);
            p_np[1] = (int64_t)(10000.0F*h);
            break;
        case 2:
            c = SDP(&bn->climate[NP_CONTINENTALNESS], px, 0, pz);
            p_np[2] = (int64_t)(10000.0F*c);
            break;
        case 3:
            e = SDP(&bn->climate[NP_EROSION], px, 0, pz);
            p_np[3] = (int64_t)(10000.0F*e);
            break;
        default:
            w = SDP(&bn->climate[NP_WEIRDNESS], px, 0, pz);
            p_np[5] = (int64_t)(10000.0F*w);
            break;
        }
        if (!(alive = hull_keep(bp, alive, k, p_np[k]))) return -2;
        th |= (k == 0) + 2*(k == 1);
        if (th == 3)
        {   // T and H known: some T×H rectangle of a candidate must hold them
            th = 4;
            for (a = alive; a; a &= a - 1)
            {
                i = __builtin_ctz(a);
                const BiomeLeafInfo *bi = bp->b[i];
                for (j = 0; j < bi->nth; j++)
                {
                    const int *r = bi->th[j];
                    if (p_np[0] >= r[0] && p_np[0] <= r[1] && p_np[1] >= r[2] && p_np[1] <= r[3])
                        break;
                }
                if (j == bi->nth)
                    alive &= ~(1U << i);
            }
            if (!alive) return -2;
        }
    }

    if (!(sample_flags & SAMPLE_NO_DEPTH))
    {
        float np_param[] = {
            c, e, -3.0F * ( fabsf( fabsf(w) - 0.6666667F ) - 0.33333334F ), w,
        };
        double off = getSpline(bn->sp, np_param) + 0.015F;
        d = 1.0 - (y * 4) / 128.0 - 83.0/160.0 + off;
    }
    p_np[4] = (int64_t)(10000.0F*d);
    if (!(alive = hull_keep(bp, alive, 4, p_np[4]))) return -2;
    for (a = alive; a; a &= a - 1)
    {   // some leaf of the candidate must contain all six parameters
        i = __builtin_ctz(a);
        const BiomeLeafInfo *bi = bp->b[i];
        for (j = 0; j < bi->nlf; j++)
        {
            const int *r = bi->lf[j];
            if (p_np[0] >= r[0] && p_np[0] <= r[1] && p_np[1] >= r[2] && p_np[1] <= r[3] &&
                p_np[2] >= r[4] && p_np[2] <= r[5] && p_np[3] >= r[6] && p_np[3] <= r[7] &&
                p_np[4] >= r[8] && p_np[4] <= r[9] && p_np[5] >= r[10] && p_np[5] <= r[11])
                break;
        }
        if (j < bi->nlf)
            break;
    }
    if (!a) return -2;

    return climateToBiome(bn->mc, (const uint64_t*)p_np, dat);
}
#undef SDP

ATTR(hot, flatten)
int climateToBiome(int mc, const uint64_t np[6], uint64_t *dat)
{
    static const BiomeTree btree18 = {
        btree18_steps, &btree18_param[0][0], btree18_nodes, btree18_order,
        sizeof(btree18_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree192 = {
        btree192_steps, &btree192_param[0][0], btree192_nodes, btree192_order,
        sizeof(btree192_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree19 = {
        btree19_steps, &btree19_param[0][0], btree19_nodes, btree19_order,
        sizeof(btree19_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree20 = {
        btree20_steps, &btree20_param[0][0], btree20_nodes, btree20_order,
        sizeof(btree20_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree21wd = {
        btree21wd_steps, &btree21wd_param[0][0], btree21wd_nodes, btree21wd_order,
        sizeof(btree21wd_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree262 = {
        btree262_steps, &btree262_param[0][0], btree262_nodes, btree262_order,
        sizeof(btree262_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree263 = {
        btree263_steps, &btree263_param[0][0], btree263_nodes, btree263_order,
        sizeof(btree263_nodes) / sizeof(uint64_t)
    };

    const BiomeTree *bt;
    int idx;

    if (mc >= MC_26_3)
        bt = &btree263;
    else if (mc >= MC_26_2)
        bt = &btree262;
    else if (mc >= MC_1_21_WD)
        bt = &btree21wd;
    else if (mc >= MC_1_20_6)
        bt = &btree20;
    else if (mc >= MC_1_19_4)
        bt = &btree19;
    else if (mc >= MC_1_19_2)
        bt = &btree192;
    else
        bt = &btree18;

    if (dat)
    {
        int alt = (int) *dat;
        uint64_t ds = get_np_dist(np, bt, alt);
        idx = get_resulting_node(np, bt, 0, alt, ds, 0);
        *dat = (uint64_t) idx;
    }
    else
    {
        idx = get_resulting_node(np, bt, 0, 0, -1, 0);
    }

    return (bt->nodes[idx] >> 48) & 0xFF;
}

/* SSA (p95): the tree search of get_resulting_node, started with a distance
 * bound and no candidate. It returns the first leaf, in traversal order, among
 * the nearest ones -- as the unbounded search does -- provided the bound is
 * above the nearest distance (else -1). Same traversal and same strict
 * comparisons as get_resulting_node; only branches whose box is at least the
 * bound away are skipped earlier. (Passing the hint as 'alt' with dist+1, as a
 * first version did, was NOT exact: a branch with no better leaf returns the
 * hint, which then lowers the bound to its own distance and hides an earlier
 * leaf at the same distance.) */
static int grn_bound(const uint64_t np[6], const BiomeTree *bt, int idx,
    uint64_t *pds, int depth)
{
    if (bt->steps[depth] == 0)
        return idx;
    uint32_t step;
    do
    {
        step = bt->steps[depth];
        depth++;
    }
    while (idx+step >= bt->len);

    uint64_t node = bt->nodes[idx];
    uint16_t inner = node >> 48;
    int leaf = -1;
    uint32_t i, n;

    for (i = 0, n = bt->order; i < n; i++)
    {
        uint64_t ds_inner = get_np_dist(np, bt, inner);
        if (ds_inner < *pds)
        {
            uint64_t ds_sub = *pds;
            int leaf2 = grn_bound(np, bt, inner, &ds_sub, depth);
            if (leaf2 >= 0)
            {
                uint64_t ds_leaf2 = (inner == leaf2) ? ds_inner : get_np_dist(np, bt, leaf2);
                if (ds_leaf2 < *pds)
                {
                    *pds = ds_leaf2;
                    leaf = leaf2;
                }
            }
        }
        inner += step;
        if (inner >= bt->len)
            break;
    }
    return leaf;
}

/* climateToBiome(mc, np, NULL) using the leaf of a neighbouring cell to get a
 * tight starting bound: same result, most branches cut at once.
 * *hint: leaf index in/out, < 0 = none. */
static int climateToBiomeHint(int mc, const uint64_t np[6], int *hint)
{
    const BiomeTree *bt = ssa_btree(mc);
    int idx = -1;
    if (*hint >= 0 && (uint32_t)*hint < bt->len)
    {
        uint64_t ds = get_np_dist(np, bt, *hint) + 1;
        idx = grn_bound(np, bt, 0, &ds, 0);
    }
    if (idx < 0)
        idx = get_resulting_node(np, bt, 0, 0, -1, 0);
    *hint = idx;
    return (bt->nodes[idx] >> 48) & 0xFF;
}


static const BiomeTree *ssa_btree(int mc)
{
    if (mc < MC_1_18)
        return NULL;
    static const BiomeTree btree18 = {
        btree18_steps, &btree18_param[0][0], btree18_nodes, btree18_order,
        sizeof(btree18_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree192 = {
        btree192_steps, &btree192_param[0][0], btree192_nodes, btree192_order,
        sizeof(btree192_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree19 = {
        btree19_steps, &btree19_param[0][0], btree19_nodes, btree19_order,
        sizeof(btree19_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree20 = {
        btree20_steps, &btree20_param[0][0], btree20_nodes, btree20_order,
        sizeof(btree20_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree21wd = {
        btree21wd_steps, &btree21wd_param[0][0], btree21wd_nodes, btree21wd_order,
        sizeof(btree21wd_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree262 = {
        btree262_steps, &btree262_param[0][0], btree262_nodes, btree262_order,
        sizeof(btree262_nodes) / sizeof(uint64_t)
    };
    static const BiomeTree btree263 = {
        btree263_steps, &btree263_param[0][0], btree263_nodes, btree263_order,
        sizeof(btree263_nodes) / sizeof(uint64_t)
    };

    const BiomeTree *bt;

    if (mc >= MC_26_3)
        bt = &btree263;
    else if (mc >= MC_26_2)
        bt = &btree262;
    else if (mc >= MC_1_21_WD)
        bt = &btree21wd;
    else if (mc >= MC_1_20_6)
        bt = &btree20;
    else if (mc >= MC_1_19_4)
        bt = &btree19;
    else if (mc >= MC_1_19_2)
        bt = &btree192;
    else
        bt = &btree18;

    return bt;
}

/* ── SSA (p93): climate noises with shared, on-demand octaves ─────────────
 * Same constants as init_climate_seed() (checked equal sample by sample
 * against setClimateParaSeed in tests/perlin_check.c). */
static int climate_para_def(int nptype, int large, const double **amp,
    int *omin, int *len, uint64_t *xl, uint64_t *xh)
{
    static const double a_temp[]  = {1.5, 0, 1, 0, 0, 0};
    static const double a_humi[]  = {1, 1, 0, 0, 0, 0};
    static const double a_cont[]  = {1, 1, 2, 2, 2, 1, 1, 1, 1};
    static const double a_eros[]  = {1, 1, 0, 1, 1};
    static const double a_weird[] = {1, 2, 1, 0, 0, 0};
    switch (nptype)
    {
    // NP_SHIFT == NP_DEPTH: setClimateParaSeed treats it as depth (C, E, W),
    // not supported here (the caller keeps setClimateParaSeed)
    case NP_TEMPERATURE:
        *amp = a_temp; *omin = large ? -12 : -10; *len = 6;
        *xl = large ? 0x944b0073edf549db : 0x5c7e6b29735f0d7f;
        *xh = large ? 0x4ff44347e9d22b96 : 0xf7d86f1bbc734988; return 1;
    case NP_HUMIDITY:
        *amp = a_humi; *omin = large ? -10 : -8; *len = 6;
        *xl = large ? 0x71b8ab943dbd5301 : 0x81bb4d22e8dc168e;
        *xh = large ? 0xbb63ddcf39ff7a2b : 0xf1c8b4bea16303cd; return 1;
    case NP_CONTINENTALNESS:
        *amp = a_cont; *omin = large ? -11 : -9; *len = 9;
        *xl = large ? 0x9a3f51a113fce8dc : 0x83886c9d0ae3a662;
        *xh = large ? 0xee2dbd157e5dcdad : 0xafa638a61b42e8ad; return 1;
    case NP_EROSION:
        *amp = a_eros; *omin = large ? -11 : -9; *len = 5;
        *xl = large ? 0x8c984b1f8702a951 : 0xd02491e6058f6fd8;
        *xh = large ? 0xead7b1f92bae535f : 0x4792512c94c17a80; return 1;
    case NP_WEIRDNESS:
        *amp = a_weird; *omin = -7; *len = 6;
        *xl = 0xefc8ef4d36102b34; *xh = 0x1beeeb324a0f24ea; return 1;
    }
    return 0;
}

int climLazyGet(ClimLazy *cl, DoublePerlinNoise *view, uint64_t seed,
    int large, int nptype, int nmax)
{
    if (!cl->valid || cl->seed != seed || cl->large != large || cl->nptype != nptype)
    {
        const double *amp;
        int omin, len, i, n;
        uint64_t xl, xh;
        if (!climate_para_def(nptype, large, &amp, &omin, &len, &xl, &xh))
            return 0;
        Xoroshiro pxr;
        xSetSeed(&pxr, seed);
        uint64_t xlo = xNextLong(&pxr);
        uint64_t xhi = xNextLong(&pxr);
        pxr.lo = xlo ^ xl;
        pxr.hi = xhi ^ xh;
        // xDoublePerlinInit: octA then octB, each xOctaveInit draws 2 longs
        cl->xloA = xNextLong(&pxr); cl->xhiA = xNextLong(&pxr);
        cl->xloB = xNextLong(&pxr); cl->xhiB = xNextLong(&pxr);
        cl->amp = amp; cl->omin = omin; cl->len = len;
        for (i = n = 0; i < len; i++)
            if (amp[i] != 0)
                cl->idx[n++] = i;
        cl->tot = n;
        cl->haveA = cl->haveB = 0;
        // xDoublePerlinInit's amplitude: len without leading/trailing zeros
        int tl = len;
        for (i = len-1; i >= 0 && amp[i] == 0.0; i--) tl--;
        for (i = 0; amp[i] == 0.0; i++) tl--;
        static const double amp_ini[] = {
            0, 5./6, 10./9, 15./12, 20./15, 25./18, 30./21, 35./24, 40./27, 45./30,
        };
        cl->amplitude = amp_ini[tl];
        cl->seed = seed; cl->large = large; cl->nptype = nptype; cl->valid = 1;
    }
    int na = cl->tot, nb = cl->tot;
    if (nmax > 0)
    {
        na = (nmax + 1) >> 1;
        nb = nmax - na;
        if (na > cl->tot) na = cl->tot;
        if (nb > cl->tot) nb = cl->tot;
    }
    while (cl->haveA < na)
    {
        xOctaveInitOne(&cl->octA[cl->haveA], cl->xloA, cl->xhiA, cl->amp,
            cl->omin, cl->len, cl->idx[cl->haveA]);
        cl->haveA++;
    }
    while (cl->haveB < nb)
    {
        xOctaveInitOne(&cl->octB[cl->haveB], cl->xloB, cl->xhiB, cl->amp,
            cl->omin, cl->len, cl->idx[cl->haveB]);
        cl->haveB++;
    }
    view->amplitude = cl->amplitude;
    view->octA.octaves = cl->octA;
    view->octA.octcnt = na;
    view->octB.octaves = cl->octB;
    view->octB.octcnt = nb;
    return 1;
}

void setClimateParaSeed(BiomeNoise *bn, uint64_t seed, int large, int nptype, int nmax)
{
    Xoroshiro pxr;
    xSetSeed(&pxr, seed);
    uint64_t xlo = xNextLong(&pxr);
    uint64_t xhi = xNextLong(&pxr);
    if (nptype == NP_DEPTH)
    {
        int n = 0;
        n += init_climate_seed(bn->climate + NP_CONTINENTALNESS,
            bn->oct + n, xlo, xhi, large,    NP_CONTINENTALNESS, nmax);
        n += init_climate_seed(bn->climate + NP_EROSION,
            bn->oct + n, xlo, xhi, large,    NP_EROSION, nmax);
        n += init_climate_seed(bn->climate + NP_WEIRDNESS,
            bn->oct + n, xlo, xhi, large,    NP_WEIRDNESS, nmax);
    }
    else
    {
        init_climate_seed(bn->climate + nptype, bn->oct, xlo, xhi, large, nptype, nmax);
    }
    bn->nptype = nptype;
}

double sampleClimatePara(const BiomeNoise *bn, int64_t *np, double x, double z)
{
    if (bn->nptype == NP_DEPTH)
    {
        float c, e, w;
        c = sampleDoublePerlin(bn->climate + NP_CONTINENTALNESS, x, 0, z);
        e = sampleDoublePerlin(bn->climate + NP_EROSION, x, 0, z);
        w = sampleDoublePerlin(bn->climate + NP_WEIRDNESS, x, 0, z);

        float np_param[] = {
            c, e, -3.0F * ( fabsf( fabsf(w) - 0.6666667F ) - 0.33333334F ), w,
        };
        double off = getSpline(bn->sp, np_param) + 0.015F;
        int y = 0;
        float d = 1.0 - (y * 4) / 128.0 - 83.0/160.0 + off;
        if (np)
        {
            np[2] = (int64_t)(10000.0F*c);
            np[3] = (int64_t)(10000.0F*e);
            np[4] = (int64_t)(10000.0F*d);
            np[5] = (int64_t)(10000.0F*w);
        }
        return d;
    }
    double p = sampleDoublePerlin(bn->climate + bn->nptype, x, 0, z);
    if (np)
        np[bn->nptype] = (int64_t)(10000.0F*p);
    return p;
}

void genBiomeNoiseChunkSection(const BiomeNoise *bn, int out[4][4][4],
    int cx, int cy, int cz, uint64_t *dat)
{
    uint64_t buf = 0;
    int i, j, k;
    int x4 = cx * 4, y4 = cy * 4, z4 = cz * 4;
    if (dat == NULL)
        dat = &buf;
    if (*dat == 0)
    {   // try to determine the ending point of the last chunk section
        sampleBiomeNoise(bn, NULL, x4+3, y4-1, z4+3, dat, 0);
    }

    // iteration order is important
    for (i = 0; i < 4; ++i) {
        for (j = 0; j < 4; ++j) {
            for (k = 0; k < 4; ++k) {
                out[i][j][k] = sampleBiomeNoise(bn, NULL, x4+i, y4+j, z4+k, dat, 0);
            }
        }
    }
}

static void genBiomeNoise3D(const BiomeNoise *bn, int *out, Range r, int opt)
{
    uint64_t dat = 0;
    uint64_t *p_dat = opt ? &dat : NULL;
    uint32_t flags = opt ? SAMPLE_NO_SHIFT : 0;
    int hint = -1;
    int i, j, k;
    int *p = out;
    int scale = r.scale > 4 ? r.scale / 4 : 1;
    int mid = scale / 2;
    for (k = 0; k < r.sy; k++)
    {
        int yk = (r.y+k);
        for (j = 0; j < r.sz; j++)
        {
            int zj = (r.z+j)*scale + mid;
            for (i = 0; i < r.sx; i++)
            {
                int xi = (r.x+i)*scale + mid;
                // SSA (p95): same results as sampleBiomeNoise(bn, NULL, xi,
                // yk, zj, p_dat, flags); faster tree search at 1:4 (hint),
                // table-gradient Perlin (faster at every scale, measured)
                *p = sampleBiomeNoiseHint(bn, xi, yk, zj, &hint, p_dat,
                        flags | SAMPLE_SSA_SCATTERED);
                p++;
            }
        }
    }
}

int genBiomeNoiseScaled(const BiomeNoise *bn, int *out, Range r, uint64_t sha)
{
    if (r.sy == 0)
        r.sy = 1;

    uint64_t siz = (uint64_t)r.sx*r.sy*r.sz;
    int i, j, k;

    if (r.scale == 1)
    {
        Range s = getVoronoiSrcRange(r);
        int *src;
        if (siz > 1)
        {   // the source range is large enough that we can try optimizing
            src = out + siz;
            genBiomeNoise3D(bn, src, s, 0);
        }
        else
        {
            src = NULL;
        }

        int *p = out;
        for (k = 0; k < r.sy; k++)
        {
            for (j = 0; j < r.sz; j++)
            {
                for (i = 0; i < r.sx; i++)
                {
                    int x4, z4, y4;
                    voronoiAccess3D(sha, r.x+i, r.y+k, r.z+j, &x4, &y4, &z4);
                    if (src)
                    {
                        x4 -= s.x; y4 -= s.y; z4 -= s.z;
                        *p = src[(int64_t)y4*s.sx*s.sz + (int64_t)z4*s.sx + x4];
                    }
                    else
                    {
                        *p = sampleBiomeNoise(bn, 0, x4, y4, z4, 0, 0);
                    }
                    p++;
                }
            }
        }
    }
    else
    {
        // There is (was?) an optimization that causes MC-241546, and should
        // not be enabled for accurate results. However, if the scale is higher
        // than 1:4, the accuracy becomes questionable anyway. Furthermore
        // situations that want to use a higher scale are usually better off
        // with a faster, if imperfect, result.
        genBiomeNoise3D(bn, out, r, r.scale > 4);
    }
    return 0;
}

static void genColumnNoise(const SurfaceNoiseBeta *snb, SeaLevelColumnNoiseBeta *dest,
    double cx, double cz, double lacmin)
{
    dest->contASample = sampleOctaveAmp(&snb->octcontA, cx, 0, cz, 0, 0, 1);
    dest->contBSample = sampleOctaveAmp(&snb->octcontB, cx, 0, cz, 0, 0, 1);
    sampleOctaveBeta17Terrain(&snb->octmin, dest->minSample, cx, cz, 0, lacmin);
    sampleOctaveBeta17Terrain(&snb->octmax, dest->maxSample, cx, cz, 0, lacmin);
    sampleOctaveBeta17Terrain(&snb->octmain, dest->mainSample, cx, cz, 1, lacmin);
}

static void processColumnNoise(double *out, const SeaLevelColumnNoiseBeta *src,
    const double climate[2])
{
    double humi = 1 - climate[0] * climate[1];
    humi *= humi;
    humi *= humi;
    humi = 1 - humi;
    double contA = (src->contASample + 256) / 512 * humi;
    contA = (contA > 1) ? 1.0 : contA;
    double contB = src->contBSample / 8000;
    if (contB < 0)
        contB = -contB * 0.3;
    contB = contB*3-2;
    if (contB < 0)
    {
        contB /= 2;
        contB = (contB < -1) ? -1.0 / 1.4 / 2 : contB / 1.4 / 2;
        contA = 0;
    }
    else
    {
        contB = (contB > 1) ? 1.0/8 : contB/8;
    }
    contA = (contA < 0) ? 0.5 : contA+0.5;
    contB = (contB * 17.0) / 16;
    contB = 17.0 / 2 + contB * 4;
    const double *low = src->minSample;
    const double *high = src->maxSample;
    const double *selector = src->mainSample;
    int i;
    for (i = 0; i <= 1; i++)
    {
        double chooseLHS;
        double procCont = ((i + 7 - contB) * 12) / contA;
        procCont = (procCont < 0) ? procCont*4 : procCont;
        double lSample = low[i] / 512;
        double hSample = high[i] / 512;
        double sSample = (selector[i] / 10 + 1) / 2;
        chooseLHS = (sSample < 0.0) ? lSample : (sSample > 1) ? hSample :
            lSample + (hSample - lSample) * sSample;
        chooseLHS -= procCont;
        out[i] = chooseLHS;
    }
}

static double lerp4(
    const double a[2], const double b[2], const double c[2], const double d[2],
    double dy, double dx, double dz)
{
    double b00 = a[0] + (a[1] - a[0]) * dy;
    double b01 = b[0] + (b[1] - b[0]) * dy;
    double b10 = c[0] + (c[1] - c[0]) * dy;
    double b11 = d[0] + (d[1] - d[0]) * dy;
    double b0 = b00 + (b10 - b00) * dz;
    double b1 = b01 + (b11 - b01) * dz;
    return b0 + (b1 - b0) * dx;
}

double approxSurfaceBeta(const BiomeNoiseBeta *bnb, const SurfaceNoiseBeta *snb,
    int x, int z)
{
    // TODO: sample vertically to get a more accurate height value
    double climate[2];
    sampleBiomeNoiseBeta(bnb, NULL, climate, x, z);
    double cols[2];
    SeaLevelColumnNoiseBeta colNoise;
    genColumnNoise(snb, &colNoise, x*0.25, z*0.25, 0);
    processColumnNoise(cols, &colNoise, climate);
    return 63 + (cols[0]*0.125 + cols[1]*0.875) * 0.5;
}

int genBiomeNoiseBetaScaled(const BiomeNoiseBeta *bnb,
    const SurfaceNoiseBeta *snb, int *out, Range r)
{
    if (!snb || r.scale >= 4)
    {
        int i, j;
        int mid = r.scale >> 1;
        for (j = 0; j < r.sz; j++)
        {
            int z = (r.z+j)*r.scale + mid;
            for (i = 0; i < r.sx; i++)
            {
                double climate[2];
                int x = (r.x+i)*r.scale + mid;
                int id = sampleBiomeNoiseBeta(bnb, NULL, climate, x, z);

                if (snb)
                {
                    double cols[2];
                    SeaLevelColumnNoiseBeta colNoise;
                    genColumnNoise(snb, &colNoise, x*0.25, z*0.25, 4.0/r.scale);
                    processColumnNoise(cols, &colNoise, climate);
                    if (cols[0]*0.125 + cols[1]*0.875 <= 0)
                        id = (climate[0] < 0.5) ? frozen_ocean : ocean;
                }
                out[(int64_t)j*r.sx + i] = id;
            }
        }
        return 0;
    }

    int cellwidth = r.scale >> 1;
    int cx1 = r.x >> (2 >> cellwidth);
    int cz1 = r.z >> (2 >> cellwidth);
    int cx2 = cx1 + (r.sx >> (2 >> cellwidth)) + 1;
    int cz2 = cz1 + (r.sz >> (2 >> cellwidth)) + 1;
    int steps = 4 >> cellwidth;
    int minDim, maxDim;
    if (cx2-cx1 > cz2-cz1) {
        maxDim = cx2-cx1;
        minDim = cz2-cz1;
    } else {
        maxDim = cz2-cz1;
        minDim = cx2-cx1;
    }
    int bufLen = minDim * 2 + 1;

    int i, j, x, z, cx, cz;
    int xStart = cx1;
    int zStart = cz1;
    int idx = 0;
    SeaLevelColumnNoiseBeta *buf = (SeaLevelColumnNoiseBeta*) (out + (int64_t)r.sx * r.sz);
    SeaLevelColumnNoiseBeta *colNoise;
    double cols[8];
    double climate[2];
    static const int off[] = { 1, 4, 7, 10, 13 };

    // Diagonal traversal of range region, in order to minimize size of saved
    // column noise buffer
    int stripe;
    for (stripe = 0; stripe < maxDim + minDim - 1; stripe++)
    {
        cx = xStart;
        cz = zStart;
        while (cx < cx2 && cz >= cz1)
        {
            int csx = (cx * 4) & ~15; // start of chunk coordinates
            int csz = (cz * 4) & ~15;
            int ci = cx & 3;
            int cj = cz & 3;

            colNoise = &buf[idx];
            if (stripe == 0)
                genColumnNoise(snb, colNoise, cx, cz, 0);
            sampleBiomeNoiseBeta(bnb, NULL, climate, csx+off[ci], csz+off[cj]);
            processColumnNoise(&cols[0], colNoise, climate);

            colNoise = &buf[(idx + minDim + 1) % bufLen];
            if (cz == cz1)
                genColumnNoise(snb, colNoise, cx+1, cz, 0);
            sampleBiomeNoiseBeta(bnb, NULL, climate, csx+off[ci+1], csz+off[cj]);
            processColumnNoise(&cols[2], colNoise, climate);

            colNoise = &buf[(idx + minDim) % bufLen];
            if (cx == cx1)
                genColumnNoise(snb, colNoise, cx, cz+1, 0);
            sampleBiomeNoiseBeta(bnb, NULL, climate, csx+off[ci], csz+off[cj+1]);
            processColumnNoise(&cols[4], colNoise, climate);

            colNoise = &buf[idx];
            genColumnNoise(snb, colNoise, cx+1, cz+1, 0);
            sampleBiomeNoiseBeta(bnb, NULL, climate, csx+off[ci+1], csz+off[cj+1]);
            processColumnNoise(&cols[6], colNoise, climate);

            // scale=1: cellwidth=0, steps=4
            // scale=4: cellwidth=2, steps=1
            for (j = 0; j < steps; j++)
            {
                z = cz * steps + j;
                if (z < r.z || z >= r.z + r.sz)
                    continue;
                for (i = 0; i < steps; i++)
                {
                    x = cx * steps + i;
                    if (x < r.x || x >= r.x + r.sx)
                        continue;
                    int mid = r.scale >> 1;
                    int bx = x * r.scale + mid;
                    int bz = z * r.scale + mid;
                    int id = sampleBiomeNoiseBeta(bnb, NULL, climate, bx, bz);
                    double dx = (bx & 3) * 0.25;
                    double dz = (bz & 3) * 0.25;
                    if (lerp4(cols+0, cols+2, cols+4, cols+6, 7./8, dx, dz) <= 0)
                        id = (climate[0] < 0.5) ? frozen_ocean : ocean;
                    out[(int64_t)(z - r.z) * r.sx + (x - r.x)] = id;
                }
            }

            cx++;
            cz--;
            idx = (idx+1) % bufLen;
        }
        if (zStart < cz2-1)
            zStart++;
        else
            xStart++;
        if (stripe+1 < minDim)
            idx = (idx + minDim-stripe-1) % bufLen;
        else if (stripe+1 > maxDim)
            idx = (idx + stripe-maxDim+2) % bufLen;
        else if (xStart > cx1)
            idx = (idx + 1) % bufLen;
    }
    return 0;
}


int getBiomeDepthAndScale(int id, double *depth, double *scale, int *grass)
{
    const int dh = 62; // default height
    double s = 0, d = 0, g = 0;
    switch (id) {
    case ocean:                         s = 0.100; d = -1.000; g = dh; break;
    case plains:                        s = 0.050; d =  0.125; g = dh; break;
    case desert:                        s = 0.050; d =  0.125; g =  0; break;
    case mountains:                     s = 0.500; d =  1.000; g = dh; break;
    case forest:                        s = 0.200; d =  0.100; g = dh; break;
    case taiga:                         s = 0.200; d =  0.200; g = dh; break;
    case swamp:                         s = 0.100; d = -0.200; g = dh; break;
    case river:                         s = 0.000; d = -0.500; g = 60; break;
    case frozen_ocean:                  s = 0.100; d = -1.000; g = dh; break;
    case frozen_river:                  s = 0.000; d = -0.500; g = 60; break;
    case snowy_tundra:                  s = 0.050; d =  0.125; g = dh; break;
    case snowy_mountains:               s = 0.300; d =  0.450; g = dh; break;
    case mushroom_fields:               s = 0.300; d =  0.200; g =  0; break;
    case mushroom_field_shore:          s = 0.025; d =  0.000; g =  0; break;
    case beach:                         s = 0.025; d =  0.000; g = 64; break;
    case desert_hills:                  s = 0.300; d =  0.450; g =  0; break;
    case wooded_hills:                  s = 0.300; d =  0.450; g = dh; break;
    case taiga_hills:                   s = 0.300; d =  0.450; g = dh; break;
    case mountain_edge:                 s = 0.300; d =  0.800; g = dh; break;
    case jungle:                        s = 0.200; d =  0.100; g = dh; break;
    case jungle_hills:                  s = 0.300; d =  0.450; g = dh; break;
    case jungle_edge:                   s = 0.200; d =  0.100; g = dh; break;
    case deep_ocean:                    s = 0.100; d = -1.800; g = dh; break;
    case stone_shore:                   s = 0.800; d =  0.100; g = 64; break;
    case snowy_beach:                   s = 0.025; d =  0.000; g = 64; break;
    case birch_forest:                  s = 0.200; d =  0.100; g = dh; break;
    case birch_forest_hills:            s = 0.300; d =  0.450; g = dh; break;
    case dark_forest:                   s = 0.200; d =  0.100; g = dh; break;
    case snowy_taiga:                   s = 0.200; d =  0.200; g = dh; break;
    case snowy_taiga_hills:             s = 0.300; d =  0.450; g = dh; break;
    case giant_tree_taiga:              s = 0.200; d =  0.200; g = dh; break;
    case giant_tree_taiga_hills:        s = 0.300; d =  0.450; g = dh; break;
    case wooded_mountains:              s = 0.500; d =  1.000; g = dh; break;
    case savanna:                       s = 0.050; d =  0.125; g = dh; break;
    case savanna_plateau:               s = 0.025; d =  1.500; g = dh; break;
    case badlands:                      s = 0.200; d =  0.100; g =  0; break;
    case wooded_badlands_plateau:       s = 0.025; d =  1.500; g =  0; break;
    case badlands_plateau:              s = 0.025; d =  1.500; g =  0; break;
    case warm_ocean:                    s = 0.100; d = -1.000; g =  0; break;
    case lukewarm_ocean:                s = 0.100; d = -1.000; g = dh; break;
    case cold_ocean:                    s = 0.100; d = -1.000; g = dh; break;
    case deep_warm_ocean:               s = 0.100; d = -1.800; g =  0; break;
    case deep_lukewarm_ocean:           s = 0.100; d = -1.800; g = dh; break;
    case deep_cold_ocean:               s = 0.100; d = -1.800; g = dh; break;
    case deep_frozen_ocean:             s = 0.100; d = -1.800; g = dh; break;
    case sunflower_plains:              s = 0.050; d =  0.125; g = dh; break;
    case desert_lakes:                  s = 0.250; d =  0.225; g =  0; break;
    case gravelly_mountains:            s = 0.500; d =  1.000; g = dh; break;
    case flower_forest:                 s = 0.400; d =  0.100; g = dh; break;
    case taiga_mountains:               s = 0.400; d =  0.300; g = dh; break;
    case swamp_hills:                   s = 0.300; d = -0.100; g = dh; break;
    case ice_spikes:                    s = 0.450; d =  0.425; g =  0; break;
    case modified_jungle:               s = 0.400; d =  0.200; g = dh; break;
    case modified_jungle_edge:          s = 0.400; d =  0.200; g = dh; break;
    case tall_birch_forest:             s = 0.400; d =  0.200; g = dh; break;
    case tall_birch_hills:              s = 0.500; d =  0.550; g = dh; break;
    case dark_forest_hills:             s = 0.400; d =  0.200; g = dh; break;
    case snowy_taiga_mountains:         s = 0.400; d =  0.300; g = dh; break;
    case giant_spruce_taiga:            s = 0.200; d =  0.200; g = dh; break;
    case giant_spruce_taiga_hills:      s = 0.200; d =  0.200; g = dh; break;
    case modified_gravelly_mountains:   s = 0.500; d =  1.000; g = dh; break;
    case shattered_savanna:             s = 1.225; d = 0.3625; g = dh; break;
    case shattered_savanna_plateau:     s = 1.212; d =  1.050; g = dh; break;
    case eroded_badlands:               s = 0.200; d =  0.100; g =  0; break;
    case modified_wooded_badlands_plateau: s = 0.300; d = 0.450; g = 0; break;
    case modified_badlands_plateau:     s = 0.300; d =  0.450; g =  0; break;
    case bamboo_jungle:                 s = 0.200; d =  0.100; g = dh; break;
    case bamboo_jungle_hills:           s = 0.300; d =  0.450; g = dh; break;
    default:
        return 0;
    }
    if (scale) *scale = s;
    if (depth) *depth = d;
    if (grass) *grass = g;
    return 1;
}


Range getVoronoiSrcRange(Range r)
{
    if (r.scale != 1)
    {
        printf("getVoronoiSrcRange() expects input range with scale 1:1\n");
        exit(1);
    }

    Range s; // output has scale 1:4
    int x = r.x - 2;
    int z = r.z - 2;
    s.scale = 4;
    s.x = x >> 2;
    s.z = z >> 2;
    s.sx = ((x + r.sx) >> 2) - s.x + 2;
    s.sz = ((z + r.sz) >> 2) - s.z + 2;
    if (r.sy < 1)
    {
        s.y = s.sy = 0;
    }
    else
    {
        int ty = r.y - 2;
        s.y = ty >> 2;
        s.sy = ((ty + r.sy) >> 2) - s.y + 2;
    }
    return s;
}


