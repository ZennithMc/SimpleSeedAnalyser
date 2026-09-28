#include "generator.h"
#include "biomes.h"
#include "finders.h"
#include "rng.h"
#include "gtypes.h"
#include <string.h>
extern int isStrongholdBiome(int mc, int id);
extern int mapContinent(const Layer*,int*,int,int,int,int);
extern int mapZoomFuzzy(const Layer*,int*,int,int,int,int);
extern int mapZoom(const Layer*,int*,int,int,int,int);
extern int mapLand(const Layer*,int*,int,int,int,int);
extern int mapIsland(const Layer*,int*,int,int,int,int);
extern int mapSnow(const Layer*,int*,int,int,int,int);
extern int mapCool(const Layer*,int*,int,int,int,int);
extern int mapHeat(const Layer*,int*,int,int,int,int);
extern int mapSpecial(const Layer*,int*,int,int,int,int);
extern int mapMushroom(const Layer*,int*,int,int,int,int);
extern int mapDeepOcean(const Layer*,int*,int,int,int,int);
extern int mapBiome(const Layer*,int*,int,int,int,int);
extern int mapNoise(const Layer*,int*,int,int,int,int);
extern int mapBamboo(const Layer*,int*,int,int,int,int);
extern int mapBiomeEdge(const Layer*,int*,int,int,int,int);
extern int mapHills(const Layer*,int*,int,int,int,int);
extern int mapRiver(const Layer*,int*,int,int,int,int);
extern int mapSmooth(const Layer*,int*,int,int,int,int);
extern int mapSunflower(const Layer*,int*,int,int,int,int);
extern int mapShore(const Layer*,int*,int,int,int,int);
extern int mapRiverMix(const Layer*,int*,int,int,int,int);
extern int mapOceanTemp(const Layer*,int*,int,int,int,int);
extern int mapOceanMix(const Layer*,int*,int,int,int,int);
static int typeOf(const Layer*L){ void*f=(void*)L->getMap;
    if(f==(void*)mapContinent)return G_CONTINENT; if(f==(void*)mapZoomFuzzy)return G_ZOOM_FUZZY;
    if(f==(void*)mapZoom)return G_ZOOM; if(f==(void*)mapLand)return G_LAND;
    if(f==(void*)mapIsland)return G_ISLAND; if(f==(void*)mapSnow)return G_SNOW;
    if(f==(void*)mapCool)return G_COOL; if(f==(void*)mapHeat)return G_HEAT;
    if(f==(void*)mapSpecial)return G_SPECIAL; if(f==(void*)mapMushroom)return G_MUSHROOM;
    if(f==(void*)mapDeepOcean)return G_DEEPOCEAN; if(f==(void*)mapBiome)return G_BIOME;
    if(f==(void*)mapNoise)return G_NOISE; if(f==(void*)mapBamboo)return G_BAMBOO;
    if(f==(void*)mapBiomeEdge)return G_BIOMEEDGE; if(f==(void*)mapHills)return G_HILLS;
    if(f==(void*)mapRiver)return G_RIVER; if(f==(void*)mapSmooth)return G_SMOOTH;
    if(f==(void*)mapSunflower)return G_SUNFLOWER; if(f==(void*)mapShore)return G_SHORE;
    if(f==(void*)mapRiverMix)return G_RIVERMIX; if(f==(void*)mapOceanTemp)return G_OCEANTEMP;
    if(f==(void*)mapOceanMix)return G_OCEANMIX; return 0; }
static void extract1(const Generator*g,int li,LayerG*gl){ const Layer*L=&g->ls.layers[li];
    gl[li].type=typeOf(L); gl[li].mc=L->mc; gl[li].zoom=L->zoom; gl[li].edge=L->edge;
    gl[li].layerSalt=L->layerSalt; gl[li].startSalt=L->startSalt; gl[li].startSeed=L->startSeed;
    gl[li].p=L->p?(int)(L->p-g->ls.layers):-1; gl[li].p2=L->p2?(int)(L->p2-g->ls.layers):-1; }
void xtract(uint64_t seed, LayerG*gl, int*oceanmix, PerlinNoiseG*perlin,
            uint64_t*vB, uint64_t*vM, int*refx, int*refz){
    Generator g; setupGenerator(&g, MC_1_16, 0); applySeed(&g, DIM_OVERWORLD, seed);
    *oceanmix = L_OCEAN_MIX_4;
    char done[GL_MAX]={0}; int stack[256],sp=0; stack[sp++]=L_OCEAN_MIX_4;
    while(sp){ int li=stack[--sp]; if(done[li])continue; done[li]=1; extract1(&g,li,gl);
        if(gl[li].p>=0)stack[sp++]=gl[li].p; if(gl[li].p2>=0)stack[sp++]=gl[li].p2; }
    memcpy(perlin, &g.ls.oceanRnd, sizeof(PerlinNoiseG));
    uint64_t b=0,m=0; for(int i=0;i<64;i++){ if(isStrongholdBiome(MC_1_16,i))b|=1ULL<<i; if(isStrongholdBiome(MC_1_16,i+128))m|=1ULL<<i; }
    *vB=b; *vM=m;
    StrongholdIter sh; initFirstStronghold(&sh, MC_1_16, seed&((1ULL<<48)-1));
    for(int i=0;i<128;i++){ nextStronghold(&sh,&g); refx[i]=sh.pos.x; refz[i]=sh.pos.z; }
}
