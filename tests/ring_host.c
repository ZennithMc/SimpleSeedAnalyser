/* Host build of the GPU ring scan (cubiomes_src/ring_gpu_kernel.cuh): the very
 * device code, compiled as C, to validate it against the C engine without a GPU.
 *   gcc -O2 -shared -fPIC -Icubiomes_src tests/ring_host.c -o tests/libringhost.so -lm */
#include <stdint.h>
#include <stdlib.h>
#include <math.h>
#include "noise_gpu_kernel.cuh"
#include "ring_gpu_kernel.cuh"

int rg_sizeof_spec(void) { return (int)sizeof(RgSpec); }

/* every position of the spiral that may pass, for one seed (grid order) */
long long rg_host_scan(uint64_t seed, RgSpec *spec, int32_t *out_xz, long long cap)
{
    BiomeNoiseG *bn = (BiomeNoiseG *)malloc(sizeof(BiomeNoiseG));
    if (!bn) return -1;
    bn->mc = 0;
    Ng_setBiomeSeed(bn, seed);
    RgOctF of[NG_OCT_TOTAL];
    for (int k = 0; k < NG_OCT_TOTAL; k++) Rg_octF(bn, k, &of[k]);
    RgN nz = { bn, of };
    Rg_prepare(spec);
    long long n = 0, tot = (long long)spec->nx * spec->nz;
    for (long long g = 0; g < tot; g++) {
        int x, z;
        if (!Rg_pos(spec, g, &x, &z)) continue;
        if (Rg_testPos(&nz, spec, x, z)) {
            if (n < cap) { out_xz[2*n] = x; out_xz[2*n+1] = z; }
            n++;
        }
    }
    free(bn);
    return n;
}
