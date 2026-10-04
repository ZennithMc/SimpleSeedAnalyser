/* perlin_check.c — exactness of the p93 noise speed-ups.
 *
 *   1. samplePerlinScattered (gradient by table) == samplePerlin (switch),
 *      and sampleDoublePerlinScattered == sampleDoublePerlin, bit for bit;
 *   2. climLazyGet (octaves shared between octave counts, built on demand)
 *      gives the same noise as setClimateParaSeed, for every climate
 *      parameter, octave count 1..18 / full, normal and Large Biomes, with the
 *      octave counts requested in random order for the same seed.
 * Every line must end with « bad 0 ».
 *
 * Build (from the SSA folder):
 *   gcc -O2 -fwrapv -Icubiomes_src -o perlin_check tests/perlin_check.c \
 *       cubiomes_src/{noise,biomes,layers,biomenoise,generator,finders,util,quadbase}.c -lm
 *   ./perlin_check
 */
#include "../cubiomes_src/generator.h"
#include <stdio.h>
#include <string.h>

static uint64_t rs = 0x2545F4914F6CDD1DULL;
static uint64_t rnd(void) { rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs; }

int main(void)
{
    int fails = 0;

    /* 1. scattered == standard */
    {
        static const double amp[] = {1, 1, 2, 2, 2, 1, 1, 1, 1};
        PerlinNoise oct[18];
        long bad = 0, n = 0;
        for (int s = 0; s < 200; s++)
        {
            Xoroshiro xr;
            xSetSeed(&xr, rnd());
            DoublePerlinNoise dp;
            xDoublePerlinInit(&dp, &xr, oct, amp, -9, 9, (s % 7) ? (int)(s % 19) : -1);
            for (int k = 0; k < 50000; k++)
            {
                double x = (double)((int64_t)(rnd() % 4000000) - 2000000) * ((k & 1) ? 1.0 : 0.37);
                double z = (double)((int64_t)(rnd() % 4000000) - 2000000) * ((k & 2) ? 1.0 : 0.13);
                double a = sampleDoublePerlin(&dp, x, 0, z);
                double b = sampleDoublePerlinScattered(&dp, x, 0, z);
                n++;
                if (a != b) bad++;
            }
        }
        printf("scattered Perlin      %ld samples  bad %ld\n", n, bad);
        fails += bad != 0;
    }

    /* 2. on-demand shared octaves == setClimateParaSeed */
    {
        static BiomeNoise bn;
        static ClimLazy cl[NP_MAX];
        memset(cl, 0, sizeof(cl));
        const int paras[] = { NP_TEMPERATURE, NP_HUMIDITY, NP_CONTINENTALNESS,
                              NP_EROSION, NP_WEIRDNESS };
        long bad = 0, n = 0;
        for (int s = 0; s < 4000; s++)
        {
            uint64_t seed = rnd();
            int large = s & 1;
            for (int q = 0; q < 8; q++)
            {
                int para = paras[rnd() % 5];
                int nmax = (int)(rnd() % 20) - 1;
                if (nmax == 0) nmax = -1;
                setClimateParaSeed(&bn, seed, large, para, nmax);
                DoublePerlinNoise v;
                if (!climLazyGet(&cl[para], &v, seed, large, para, nmax) ||
                    v.octA.octcnt != bn.climate[para].octA.octcnt ||
                    v.octB.octcnt != bn.climate[para].octB.octcnt ||
                    v.amplitude != bn.climate[para].amplitude)
                {
                    bad++;
                    continue;
                }
                for (int k = 0; k < 20; k++)
                {
                    double x = (double)((int64_t)(rnd() % 200000) - 100000);
                    double z = (double)((int64_t)(rnd() % 200000) - 100000);
                    n++;
                    if (sampleDoublePerlin(&bn.climate[para], x, 0, z) !=
                        sampleDoublePerlin(&v, x, 0, z))
                        bad++;
                }
            }
        }
        printf("shared climate octaves %ld samples  bad %ld\n", n, bad);
        fails += bad != 0;
    }
    return fails ? 1 : 0;
}
