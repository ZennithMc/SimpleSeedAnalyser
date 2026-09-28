// biome_gpu_bridge.c — host-side template extraction for the GPU biome snap.
// Compiled INTO libcubiomes; Python calls biome_gpu_get_template(mc,...) once per
// version to get the seed-INDEPENDENT layer template + validB/validM + oceanmix,
// then passes them to biome_gpu.dll's biome_gpu_strongholds128.
#include "generator.h"
#include "gtypes.h"
#ifdef _WIN32
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT __attribute__((visibility("default")))
#endif
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
static int bg_typeOf(const Layer*L){ void*f=(void*)L->getMap;
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
// returns 1 if this version is supported (layer-stack: 1.13-1.17), 0 otherwise (e.g. 1.18+ noise)
EXPORT int biome_gpu_get_template(int mc, LayerG* tmpl, int* oceanmix, uint64_t* vB, uint64_t* vM){
    if(mc < MC_1_13 || mc > MC_1_17) return 0;     // layer-stack overworld only
    Generator g; setupGenerator(&g, mc, 0);        // layerSalt set here (seed-independent)
    for(int i=0;i<GL_MAX;i++){ tmpl[i].type=0; tmpl[i].p=-1; tmpl[i].p2=-1; }
    *oceanmix = L_OCEAN_MIX_4;
    char done[GL_MAX]={0}; int stack[256],sp=0; stack[sp++]=L_OCEAN_MIX_4;
    while(sp){ int li=stack[--sp]; if(li<0||li>=GL_MAX||done[li])continue; done[li]=1;
        const Layer*L=&g.ls.layers[li];
        tmpl[li].type=bg_typeOf(L); tmpl[li].mc=L->mc; tmpl[li].zoom=L->zoom; tmpl[li].edge=L->edge;
        tmpl[li].layerSalt=L->layerSalt; tmpl[li].startSalt=0; tmpl[li].startSeed=0;
        tmpl[li].p=L->p?(int)(L->p-g.ls.layers):-1; tmpl[li].p2=L->p2?(int)(L->p2-g.ls.layers):-1;
        if(tmpl[li].p>=0)stack[sp++]=tmpl[li].p; if(tmpl[li].p2>=0)stack[sp++]=tmpl[li].p2; }
    uint64_t b=0,m=0; for(int i=0;i<64;i++){ if(isStrongholdBiome(mc,i))b|=1ULL<<i; if(isStrongholdBiome(mc,i+128))m|=1ULL<<i; }
    *vB=b; *vM=m;
    return 1;
}
