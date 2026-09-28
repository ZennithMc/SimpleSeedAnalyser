#include "terrainnoise.h"
#include <stdio.h>
#include <stdint.h>
#define NCOL (48+1)
int main(void){
    TerrainNoise tn;
    if(!setupTerrainNoise(&tn, MC_1_18, 0)){ printf("setup fail\n"); return 1; }
    if(!initTerrainNoise(&tn, (uint64_t)100, DIM_OVERWORLD)){ printf("init fail\n"); return 1; }
    int C[4][2]={{0,0},{2,32},{113,126},{107,223}}; int O[4]={94,72,65,88};
    for(int i=0;i<4;i++){
        int x=C[i][0], z=C[i][1];
        int cx=x>>2, cz=z>>2;
        double d00[NCOL],d01[NCOL],d10[NCOL],d11[NCOL];
        sampleNoiseColumn(&tn, cx,   cz,   d00);
        sampleNoiseColumn(&tn, cx,   cz+1, d01);
        sampleNoiseColumn(&tn, cx+1, cz,   d10);
        sampleNoiseColumn(&tn, cx+1, cz+1, d11);
        int blocks[384];
        int y = generateColumn(x, z, blocks, d00, d01, d10, d11, 1);
        printf("(%3d,%3d) xpple_Y=%3d  oracle=%2d  diff=%+d\n", x, z, y-64, O[i], (y-64)-O[i]);
    }
    return 0;
}
