#include "biome_gpu_kernel.cuh"
#include "gtypes.h"
#include <stdio.h>
#include <string.h>
extern void xtract(uint64_t, LayerG*, int*, PerlinNoiseG*, uint64_t*, uint64_t*, int*, int*);
static char scratch[ GL_MAX*sizeof(LayerG)+sizeof(PerlinNoiseG)+60000*sizeof(int) ];
int main(){
    uint64_t seeds[]={1000ULL,1234567ULL,42ULL,8675309ULL,999999999ULL};
    int tot=0,totm=0;
    for(int si=0;si<5;si++){
        LayerG gl[GL_MAX]; PerlinNoiseG pn; int omix; uint64_t vB,vM; int rx[128],rz[128];
        memset(gl,0,sizeof(gl));
        xtract(seeds[si], gl, &omix, &pn, &vB, &vM, rx, rz);
        memcpy(GL_TMPL, gl, sizeof(gl)); GL_OCEANMIX=omix; (void)pn;
        int ox[128],oz[128];
        S_strongholds128(MC_1_16, seeds[si], vB, vM, (void*)scratch, ox, oz);
        int mism=0; for(int i=0;i<128;i++){ tot++; if(ox[i]!=rx[i]||oz[i]!=rz[i]){mism++;totm++;} }
        printf("seed %-12llu : %d/128 %s\n",(unsigned long long)seeds[si],128-mism,mism?"*** MISMATCH ***":"OK");
    }
    printf("\n%s (%d/%d)\n", totm==0?"==== .cuh DEVICE CODE BIT-EXACT (self-contained) ====":"==== MISMATCH ====", tot-totm, tot);
    return totm?1:0;
}
