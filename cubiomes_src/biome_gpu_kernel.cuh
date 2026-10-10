// biome_gpu_kernel.cuh — self-contained 1.16 biome generation + stronghold snap,
// compiles as CUDA __device__ (under nvcc) OR plain C (for sandbox validation).
// All device code is dependency-free: own biome-id constants, ported id-helpers,
// ported samplePerlin. Host side extracts the LayerG table + oceanRnd Perlin +
// validB/validM masks from cubiomes and uploads them (GPUMEM symbols).
// Validated bit-exact vs cubiomes (biome gen 25 layers + 128-stronghold snap).
#ifndef BIOME_GPU_KERNEL_CUH
#define BIOME_GPU_KERNEL_CUH
#include <stdint.h>
#include <math.h>
#ifndef __CUDACC__
#include <string.h>
#endif

#ifdef __CUDACC__
  #define GPUDEV __device__
  #define GPUMEM __constant__
#else
  #define GPUDEV
  #define GPUMEM
#endif

// ---- biome id constants (cubiomes values, hardcoded) ----
#define ocean 0
#define plains 1
#define desert 2
#define mountains 3
#define forest 4
#define taiga 5
#define swamp 6
#define river 7
#define nether_wastes 8
#define frozen_ocean 10
#define frozen_river 11
#define snowy_tundra 12
#define snowy_mountains 13
#define mushroom_fields 14
#define mushroom_field_shore 15
#define beach 16
#define desert_hills 17
#define wooded_hills 18
#define taiga_hills 19
#define mountain_edge 20
#define jungle 21
#define jungle_hills 22
#define jungle_edge 23
#define deep_ocean 24
#define stone_shore 25
#define snowy_beach 26
#define birch_forest 27
#define birch_forest_hills 28
#define dark_forest 29
#define snowy_taiga 30
#define snowy_taiga_hills 31
#define giant_tree_taiga 32
#define giant_tree_taiga_hills 33
#define wooded_mountains 34
#define savanna 35
#define savanna_plateau 36
#define badlands 37
#define wooded_badlands_plateau 38
#define badlands_plateau 39
#define warm_ocean 44
#define lukewarm_ocean 45
#define cold_ocean 46
#define deep_warm_ocean 47
#define deep_lukewarm_ocean 48
#define deep_cold_ocean 49
#define deep_frozen_ocean 50
#define sunflower_plains 129
#define desert_lakes 130
#define gravelly_mountains 131
#define flower_forest 132
#define taiga_mountains 133
#define swamp_hills 134
#define ice_spikes 140
#define modified_jungle 149
#define modified_jungle_edge 151
#define tall_birch_forest 155
#define tall_birch_hills 156
#define dark_forest_hills 157
#define snowy_taiga_mountains 158
#define giant_spruce_taiga 160
#define giant_spruce_taiga_hills 161
#define modified_gravelly_mountains 162
#define shattered_savanna 163
#define shattered_savanna_plateau 164
#define eroded_badlands 165
#define modified_wooded_badlands_plateau 166
#define modified_badlands_plateau 167
#define bamboo_jungle 168
#define bamboo_jungle_hills 169
#define soul_sand_valley 170
#define crimson_forest 171
#define warped_forest 172
#define basalt_deltas 173
#define mesa 37
#define none (-1)
// climate temp categories (layers.h enum)
#define Oceanic 0
#define Warm 1
#define Lush 2
#define Cold 3
#define Freezing 4
#define Special 5
// version constants
#define MC_1_6 9
#define MC_1_7 10
#define MC_1_9 12
#define MC_1_10 13
#define MC_1_13 16
#define MC_1_15 18
#define MC_1_16_1 19
#define MC_1_16 20
#define MC_1_18 22

#include "gtypes.h"   // PerlinNoiseG, LayerG, type enum, GL_MAX
GPUMEM LayerG GL_TMPL[GL_MAX];   // seed-INDEPENDENT template (layerSalt,type,zoom,edge,p,p2)
GPUMEM int GL_OCEANMIX;        // index of the scale-4 entry layer (L_OCEAN_MIX_4)

// ===================== RNG primitives =====================
GPUDEV static inline uint64_t G_step(uint64_t s, uint64_t salt){
    return s*(s*6364136223846793005ULL+1442695040888963407ULL)+salt;
}
GPUDEV static inline int G_firstIsZero(uint64_t s, int mod){
    return (int)(((int64_t)s>>24)%mod)==0;
}
GPUDEV static inline int G_firstInt(uint64_t s, int mod){
    int r=(int)(((int64_t)s>>24)%mod); if(r<0)r+=mod; return r;
}
GPUDEV static inline uint64_t G_chunkSeed(uint64_t ss,int x,int z){
    uint64_t cs=ss+x; cs=G_step(cs,z); cs=G_step(cs,x); cs=G_step(cs,z); return cs;
}
GPUDEV static inline int isAny4(int id,int a,int b,int c,int d){ return id==a||id==b||id==c||id==d; }
GPUDEV static inline void G_movints(int* dst, const int* src, int n){ if(dst<src){ for(int i=0;i<n;i++) dst[i]=src[i]; } else { for(int i=n-1;i>=0;i--) dst[i]=src[i]; } }

// ===================== id-helpers + samplePerlin =====================
GPUDEV static int D_getCategory(int mc,int id){
    switch(id){
    case beach: case snowy_beach: return beach;
    case desert: case desert_hills: case desert_lakes: return desert;
    case mountains: case mountain_edge: case wooded_mountains: case gravelly_mountains:
    case modified_gravelly_mountains: return mountains;
    case forest: case wooded_hills: case birch_forest: case birch_forest_hills:
    case dark_forest: case flower_forest: case tall_birch_forest: case tall_birch_hills:
    case dark_forest_hills: return forest;
    case snowy_tundra: case snowy_mountains: case ice_spikes: return snowy_tundra;
    case jungle: case jungle_hills: case jungle_edge: case modified_jungle:
    case modified_jungle_edge: case bamboo_jungle: case bamboo_jungle_hills: return jungle;
    case badlands: case eroded_badlands: case modified_wooded_badlands_plateau:
    case modified_badlands_plateau: return mesa;
    case wooded_badlands_plateau: case badlands_plateau: return mc<=MC_1_16_1?mesa:badlands_plateau;
    case mushroom_fields: case mushroom_field_shore: return mushroom_fields;
    case stone_shore: return stone_shore;
    case ocean: case frozen_ocean: case deep_ocean: case warm_ocean: case lukewarm_ocean:
    case cold_ocean: case deep_warm_ocean: case deep_lukewarm_ocean: case deep_cold_ocean:
    case deep_frozen_ocean: return ocean;
    case plains: case sunflower_plains: return plains;
    case river: case frozen_river: return river;
    case savanna: case savanna_plateau: case shattered_savanna: case shattered_savanna_plateau: return savanna;
    case swamp: case swamp_hills: return swamp;
    case taiga: case taiga_hills: case snowy_taiga: case snowy_taiga_hills: case giant_tree_taiga:
    case giant_tree_taiga_hills: case taiga_mountains: case snowy_taiga_mountains:
    case giant_spruce_taiga: case giant_spruce_taiga_hills: return taiga;
    case nether_wastes: case soul_sand_valley: case crimson_forest: case warped_forest: case basalt_deltas: return nether_wastes;
    default: return none;
    }
}
GPUDEV static int D_areSimilar(int mc,int a,int b){
    if(a==b)return 1;
    if(mc<=MC_1_16_1){ if(a==wooded_badlands_plateau||a==badlands_plateau) return b==wooded_badlands_plateau||b==badlands_plateau; }
    return D_getCategory(mc,a)==D_getCategory(mc,b);
}
GPUDEV static int D_isMesa(int id){ switch(id){case badlands:case eroded_badlands:case modified_wooded_badlands_plateau:case modified_badlands_plateau:case wooded_badlands_plateau:case badlands_plateau:return 1;default:return 0;} }
GPUDEV static int D_isSnowy(int id){ switch(id){case frozen_ocean:case frozen_river:case snowy_tundra:case snowy_mountains:case snowy_beach:case snowy_taiga:case snowy_taiga_hills:case ice_spikes:case snowy_taiga_mountains:return 1;default:return 0;} }
GPUDEV static int D_isShallowOcean(int id){ const uint64_t b=(1ULL<<ocean)|(1ULL<<frozen_ocean)|(1ULL<<warm_ocean)|(1ULL<<lukewarm_ocean)|(1ULL<<cold_ocean); return (uint32_t)id<64&&((1ULL<<id)&b); }
GPUDEV static int D_isDeepOcean(int id){ const uint64_t b=(1ULL<<deep_ocean)|(1ULL<<deep_warm_ocean)|(1ULL<<deep_lukewarm_ocean)|(1ULL<<deep_cold_ocean)|(1ULL<<deep_frozen_ocean); return (uint32_t)id<64&&((1ULL<<id)&b); }
GPUDEV static int D_isOceanic(int id){ const uint64_t b=(1ULL<<ocean)|(1ULL<<frozen_ocean)|(1ULL<<warm_ocean)|(1ULL<<lukewarm_ocean)|(1ULL<<cold_ocean)|(1ULL<<deep_ocean)|(1ULL<<deep_warm_ocean)|(1ULL<<deep_lukewarm_ocean)|(1ULL<<deep_cold_ocean)|(1ULL<<deep_frozen_ocean); return (uint32_t)id<64&&((1ULL<<id)&b); }
GPUDEV static int D_getMutated(int mc,int id){
    switch(id){
    case plains:return sunflower_plains; case desert:return desert_lakes; case mountains:return gravelly_mountains;
    case forest:return flower_forest; case taiga:return taiga_mountains; case swamp:return swamp_hills;
    case snowy_tundra:return ice_spikes; case jungle:return modified_jungle; case jungle_edge:return modified_jungle_edge;
    case birch_forest:return (mc>=MC_1_9&&mc<=MC_1_10)?tall_birch_hills:tall_birch_forest;
    case birch_forest_hills:return (mc>=MC_1_9&&mc<=MC_1_10)?none:tall_birch_hills;
    case dark_forest:return dark_forest_hills; case snowy_taiga:return snowy_taiga_mountains;
    case giant_tree_taiga:return giant_spruce_taiga; case giant_tree_taiga_hills:return giant_spruce_taiga_hills;
    case wooded_mountains:return modified_gravelly_mountains; case savanna:return shattered_savanna;
    case savanna_plateau:return shattered_savanna_plateau; case badlands:return eroded_badlands;
    case wooded_badlands_plateau:return modified_wooded_badlands_plateau; case badlands_plateau:return modified_badlands_plateau;
    default:return none;
    }
}
// ===== ported samplePerlin (-> __device__) : verbatim from noise.c =====
GPUDEV static double D_lerp(double p,double a,double b){ return a+p*(b-a); }
GPUDEV static double D_ilerp(uint8_t idx,double a,double b,double c){
    switch(idx&0xf){
    case 0:return a+b; case 1:return -a+b; case 2:return a-b; case 3:return -a-b;
    case 4:return a+c; case 5:return -a+c; case 6:return a-c; case 7:return -a-c;
    case 8:return b+c; case 9:return -b+c; case 10:return b-c; case 11:return -b-c;
    case 12:return a+b; case 13:return -b+c; case 14:return -a+b; case 15:return -b-c;
    } return 0;
}
GPUDEV static double D_samplePerlin(const PerlinNoiseG *noise,double d1,double d2,double d3,double yamp,double ymin){
    uint8_t h1,h2,h3; double t1,t2,t3;
    if(d2==0.0){ d2=noise->d2; h2=noise->h2; t2=noise->t2; }
    else { d2+=noise->b; double i2=floor(d2); d2-=i2; h2=(int)i2; t2=d2*d2*d2*(d2*(d2*6.0-15.0)+10.0); }
    d1+=noise->a; d3+=noise->c;
    double i1=floor(d1), i3=floor(d3); d1-=i1; d3-=i3; h1=(int)i1; h3=(int)i3;
    t1=d1*d1*d1*(d1*(d1*6.0-15.0)+10.0); t3=d3*d3*d3*(d3*(d3*6.0-15.0)+10.0);
    if(yamp){ double yc=ymin<d2?ymin:d2; d2-=floor(yc/yamp)*yamp; }
    const uint8_t *idx=noise->d;
    uint8_t a1=idx[h1]+h2, b1=idx[h1+1]+h2;
    uint8_t a2=idx[a1]+h3, b2=idx[b1]+h3, a3=idx[a1+1]+h3, b3=idx[b1+1]+h3;
    double l1=D_ilerp(idx[a2],d1,d2,d3), l2=D_ilerp(idx[b2],d1-1,d2,d3);
    double l3=D_ilerp(idx[a3],d1,d2-1,d3), l4=D_ilerp(idx[b3],d1-1,d2-1,d3);
    double l5=D_ilerp(idx[a2+1],d1,d2,d3-1), l6=D_ilerp(idx[b2+1],d1-1,d2,d3-1);
    double l7=D_ilerp(idx[a3+1],d1,d2-1,d3-1), l8=D_ilerp(idx[b3+1],d1-1,d2-1,d3-1);
    l1=D_lerp(t1,l1,l2); l3=D_lerp(t1,l3,l4); l5=D_lerp(t1,l5,l6); l7=D_lerp(t1,l7,l8);
    l1=D_lerp(t2,l1,l3); l5=D_lerp(t2,l5,l7);
    return D_lerp(t3,l1,l5);
}

// ===================== forward decl + biome gen =====================
GPUDEV static int G_genArea(const LayerG* _gl, const PerlinNoiseG* _pn, int li, int *out, int x, int z, int w, int h);
GPUDEV static const int G_warm[] = {desert, desert, desert, savanna, savanna, plains};
GPUDEV static const int G_lush[] = {forest, dark_forest, mountains, plains, birch_forest, swamp};
GPUDEV static const int G_cold[] = {forest, mountains, taiga, plains};
GPUDEV static const int G_snow[] = {snowy_tundra, snowy_tundra, snowy_tundra, snowy_taiga};

// ===================== ported map functions ===================================
GPUDEV static int Gm_continent(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    uint64_t ss=l->startSeed;
    for(int j=0;j<h;j++) for(int i=0;i<w;i++)
        out[j*w+i]=G_firstIsZero(G_chunkSeed(ss,i+x,j+z),10);
    if(x>-w&&x<=0&&z>-h&&z<=0) out[-z*(int64_t)w-x]=1;
    return 0;
}

GPUDEV static int Gm_zoomFuzzy(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x>>1, pZ=z>>1;
    int64_t pW=((x+w)>>1)-pX+1, pH=((z+h)>>1)-pZ+1, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    int64_t newW=pW*2, idx; int v00,v01,v10,v11;
    int *buf=out+pW*pH;
    const uint32_t st=(uint32_t)l->startSalt, ss=(uint32_t)l->startSeed;
    for(j=0;j<pH;j++){
        idx=(j*2)*newW; v00=out[(j+0)*pW]; v01=out[(j+1)*pW];
        for(i=0;i<pW;i++,v00=v10,v01=v11){
            v10=out[i+1+(j+0)*pW]; v11=out[i+1+(j+1)*pW];
            if(v00==v01&&v00==v10&&v00==v11){buf[idx]=v00;buf[idx+1]=v00;buf[idx+newW]=v00;buf[idx+newW+1]=v00;idx+=2;continue;}
            int chunkX=(i+pX)*2, chunkZ=(j+pZ)*2;
            uint32_t cs=ss; cs+=chunkX; cs*=cs*1284865837+4150755663; cs+=chunkZ;
            cs*=cs*1284865837+4150755663; cs+=chunkX; cs*=cs*1284865837+4150755663; cs+=chunkZ;
            buf[idx]=v00; buf[idx+newW]=(cs>>24)&1?v01:v00; idx++;
            cs*=cs*1284865837+4150755663; cs+=st; buf[idx]=(cs>>24)&1?v10:v00;
            cs*=cs*1284865837+4150755663; cs+=st; int r=(cs>>24)&3;
            buf[idx+newW]=r==0?v00:r==1?v10:r==2?v01:v11; idx++;
        }
    }
    for(j=0;j<h;j++) G_movints(&out[j*w],&buf[(j+(z&1))*newW+(x&1)],w);
    return 0;
}

GPUDEV static inline int G_select4(uint32_t cs,uint32_t st,int v00,int v01,int v10,int v11){
    int cv00=(v00==v10)+(v00==v01)+(v00==v11), cv10=(v10==v01)+(v10==v11), cv01=(v01==v11);
    if(cv00>cv10&&cv00>cv01)return v00; else if(cv10>cv00)return v10; else if(cv01>cv00)return v01;
    cs*=cs*1284865837+4150755663; cs+=st; int r=(cs>>24)&3; return r==0?v00:r==1?v10:r==2?v01:v11;
}
GPUDEV static int Gm_zoom(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x>>1, pZ=z>>1;
    int64_t pW=((x+w)>>1)-pX+1, pH=((z+h)>>1)-pZ+1, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    int64_t newW=pW*2, idx; int v00,v01,v10,v11;
    int *buf=out+pW*pH;
    const uint32_t st=(uint32_t)l->startSalt, ss=(uint32_t)l->startSeed;
    for(j=0;j<pH;j++){
        idx=(j*2)*newW; v00=out[(j+0)*pW]; v01=out[(j+1)*pW];
        for(i=0;i<pW;i++,v00=v10,v01=v11){
            v10=out[i+1+(j+0)*pW]; v11=out[i+1+(j+1)*pW];
            if(v00==v01&&v00==v10&&v00==v11){buf[idx]=v00;buf[idx+1]=v00;buf[idx+newW]=v00;buf[idx+newW+1]=v00;idx+=2;continue;}
            int chunkX=(i+pX)*2, chunkZ=(j+pZ)*2;
            uint32_t cs=ss; cs+=chunkX; cs*=cs*1284865837+4150755663; cs+=chunkZ;
            cs*=cs*1284865837+4150755663; cs+=chunkX; cs*=cs*1284865837+4150755663; cs+=chunkZ;
            buf[idx]=v00; buf[idx+newW]=(cs>>24)&1?v01:v00; idx++;
            cs*=cs*1284865837+4150755663; cs+=st; buf[idx]=(cs>>24)&1?v10:v00;
            buf[idx+newW]=G_select4(cs,st,v00,v01,v10,v11); idx++;
        }
    }
    for(j=0;j<h;j++) G_movints(&out[j*w],&buf[(j+(z&1))*newW+(x&1)],w);
    return 0;
}

GPUDEV static int Gm_land(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    uint64_t st=l->startSalt, ss=l->startSeed, cs;
    for(j=0;j<h;j++){
        int *vz0=out+(j+0)*pW,*vz1=out+(j+1)*pW,*vz2=out+(j+2)*pW;
        int v00=vz0[0],vt0=vz0[1],v02=vz2[0],vt2=vz2[1],v20,v22,v11,v;
        for(i=0;i<w;i++){
            v11=vz1[i+1]; v20=vz0[i+2]; v22=vz2[i+2]; v=v11;
            switch(v11){
            case ocean:
                if(v00||v20||v02||v22){
                    cs=G_chunkSeed(ss,i+x,j+z); int inc=0; v=1;
                    if(v00!=ocean){++inc;v=v00;cs=G_step(cs,st);}
                    if(v20!=ocean){if(++inc==1||G_firstIsZero(cs,2))v=v20; cs=G_step(cs,st);}
                    if(v02!=ocean){switch(++inc){case 1:v=v02;break;case 2:if(G_firstIsZero(cs,2))v=v02;break;default:if(G_firstIsZero(cs,3))v=v02;} cs=G_step(cs,st);}
                    if(v22!=ocean){switch(++inc){case 1:v=v22;break;case 2:if(G_firstIsZero(cs,2))v=v22;break;case 3:if(G_firstIsZero(cs,3))v=v22;break;default:if(G_firstIsZero(cs,4))v=v22;} cs=G_step(cs,st);}
                    if(v!=forest){ if(!G_firstIsZero(cs,3)) v=ocean; }
                }
                break;
            case forest: break;
            default:
                if(v00==0||v20==0||v02==0||v22==0){ cs=G_chunkSeed(ss,i+x,j+z); if(G_firstIsZero(cs,5)) v=0; }
            }
            out[i+j*w]=v; v00=vt0; vt0=v20; v02=vt2; vt2=v22;
        }
    }
    return 0;
}

GPUDEV static int Gm_island(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v11=out[i+1+(j+1)*pW]; out[i+j*w]=v11;
        if(v11==Oceanic){
            if(out[i+1+(j+0)*pW]!=Oceanic)continue;
            if(out[i+2+(j+1)*pW]!=Oceanic)continue;
            if(out[i+0+(j+1)*pW]!=Oceanic)continue;
            if(out[i+1+(j+2)*pW]!=Oceanic)continue;
            cs=G_chunkSeed(ss,i+x,j+z); if(G_firstIsZero(cs,2)) out[i+j*w]=1;
        }
    }
    return 0;
}

GPUDEV static int Gm_snow(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v11=out[i+1+(j+1)*pW];
        if(!D_isShallowOcean(v11)){
            cs=G_chunkSeed(ss,i+x,j+z); int r=G_firstInt(cs,6);
            if(r==0)v11=Freezing; else if(r<=1)v11=Cold; else v11=Warm;
        }
        out[i+j*w]=v11;
    }
    return 0;
}

GPUDEV static int Gm_cool(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v11=out[i+1+(j+1)*pW];
        if(v11==Warm){
            int v10=out[i+1+(j+0)*pW],v21=out[i+2+(j+1)*pW],v01=out[i+0+(j+1)*pW],v12=out[i+1+(j+2)*pW];
            if(isAny4(Cold,v10,v21,v01,v12)||isAny4(Freezing,v10,v21,v01,v12)) v11=Lush;
        }
        out[i+j*w]=v11;
    }
    return 0;
}

GPUDEV static int Gm_heat(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v11=out[i+1+(j+1)*pW];
        if(v11==Freezing){
            int v10=out[i+1+(j+0)*pW],v21=out[i+2+(j+1)*pW],v01=out[i+0+(j+1)*pW],v12=out[i+1+(j+2)*pW];
            if(isAny4(Warm,v10,v21,v01,v12)||isAny4(Lush,v10,v21,v01,v12)) v11=Cold;
        }
        out[i+j*w]=v11;
    }
    return 0;
}

GPUDEV static int Gm_special(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j;
    int err=G_genArea(_gl,_pn,l->p,out,x,z,w,h); if(err)return err;
    uint64_t st=l->startSalt, ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v=out[i+j*w]; if(v==Oceanic)continue;
        cs=G_chunkSeed(ss,i+x,j+z);
        if(G_firstIsZero(cs,13)){
            cs=G_step(cs,st);
            v |= (uint32_t)(1+G_firstInt(cs,15))<<8 & 0xf00;
            out[i+j*w]=v;
        }
    }
    return 0;
}

GPUDEV static int Gm_mushroom(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v11=out[i+1+(j+1)*pW];
        if(v11==0 && !out[i+0+(j+0)*pW] && !out[i+2+(j+0)*pW] && !out[i+0+(j+2)*pW] && !out[i+2+(j+2)*pW]){
            cs=G_chunkSeed(ss,i+x,j+z); if(G_firstIsZero(cs,100)) v11=mushroom_fields;
        }
        out[i+j*w]=v11;
    }
    return 0;
}

GPUDEV static int Gm_deepOcean(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v11=out[(i+1)+(j+1)*pW];
        if(D_isShallowOcean(v11)){
            int oc=0;
            if(D_isShallowOcean(out[(i+1)+(j+0)*pW]))oc++;
            if(D_isShallowOcean(out[(i+2)+(j+1)*pW]))oc++;
            if(D_isShallowOcean(out[(i+0)+(j+1)*pW]))oc++;
            if(D_isShallowOcean(out[(i+1)+(j+2)*pW]))oc++;
            if(oc>=4){
                switch(v11){
                case warm_ocean: v11=deep_warm_ocean; break;
                case lukewarm_ocean: v11=deep_lukewarm_ocean; break;
                case ocean: v11=deep_ocean; break;
                case cold_ocean: v11=deep_cold_ocean; break;
                case frozen_ocean: v11=deep_frozen_ocean; break;
                default: v11=deep_ocean;
                }
            }
        }
        out[i+j*w]=v11;
    }
    return 0;
}

GPUDEV static int Gm_biome(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j;
    int err=G_genArea(_gl,_pn,l->p,out,x,z,w,h); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    // 1.16 path (mc > MC_1_6): isOceanic / mushroom shortcut + Warm/Lush/Cold/Freezing
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int64_t idx=i+j*w; int id=out[idx]; int v;
        int hasHighBit=(id&0xf00); id&=~0xf00;
        if(D_isOceanic(id)||id==mushroom_fields){ out[idx]=id; continue; }
        cs=G_chunkSeed(ss,i+x,j+z);
        switch(id){
        case Warm:
            if(hasHighBit) v=G_firstIsZero(cs,3)?badlands_plateau:wooded_badlands_plateau;
            else v=G_warm[G_firstInt(cs,6)];
            break;
        case Lush:
            if(hasHighBit) v=jungle; else v=G_lush[G_firstInt(cs,6)];
            break;
        case Cold:
            if(hasHighBit) v=giant_tree_taiga; else v=G_cold[G_firstInt(cs,4)];
            break;
        case Freezing:
            v=G_snow[G_firstInt(cs,4)];
            break;
        default:
            v=mushroom_fields;
        }
        out[idx]=v;
    }
    return 0;
}

GPUDEV static int Gm_noise(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j;
    int err=G_genArea(_gl,_pn,l->p,out,x,z,w,h); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    int mod=299999; // mc > MC_1_6
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        if(out[i+j*w]>0){ cs=G_chunkSeed(ss,i+x,j+z); out[i+j*w]=G_firstInt(cs,mod)+2; }
        else out[i+j*w]=0;
    }
    return 0;
}

GPUDEV static int Gm_bamboo(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j;
    int err=G_genArea(_gl,_pn,l->p,out,x,z,w,h); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int64_t idx=i+j*w; if(out[idx]!=jungle)continue;
        cs=G_chunkSeed(ss,i+x,j+z); if(G_firstIsZero(cs,10)) out[idx]=bamboo_jungle;
    }
    return 0;
}

GPUDEV static inline int G_replaceEdge(int *out,int idx,int mc,int v10,int v21,int v01,int v12,int id,int baseID,int edgeID){
    if(id!=baseID)return 0;
    if(D_areSimilar(mc,v10,baseID)&&D_areSimilar(mc,v21,baseID)&&D_areSimilar(mc,v01,baseID)&&D_areSimilar(mc,v12,baseID))
        out[idx]=id;
    else out[idx]=edgeID;
    return 1;
}
GPUDEV static int Gm_biomeEdge(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j; int mc=l->mc;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    for(j=0;j<h;j++){
        int *vz0=out+(j+0)*pW,*vz1=out+(j+1)*pW,*vz2=out+(j+2)*pW;
        for(i=0;i<w;i++){
            int v11=vz1[i+1],v10=vz0[i+1],v21=vz1[i+2],v01=vz1[i+0],v12=vz2[i+1];
            if(!G_replaceEdge(out,i+j*w,mc,v10,v21,v01,v12,v11,wooded_badlands_plateau,badlands) &&
               !G_replaceEdge(out,i+j*w,mc,v10,v21,v01,v12,v11,badlands_plateau,badlands) &&
               !G_replaceEdge(out,i+j*w,mc,v10,v21,v01,v12,v11,giant_tree_taiga,taiga)){
                if(v11==desert){
                    if(!isAny4(snowy_tundra,v10,v21,v01,v12)) out[i+j*w]=v11;
                    else out[i+j*w]=wooded_mountains;
                } else if(v11==swamp){
                    if(!isAny4(desert,v10,v21,v01,v12)&&!isAny4(snowy_taiga,v10,v21,v01,v12)&&!isAny4(snowy_tundra,v10,v21,v01,v12)){
                        if(!isAny4(jungle,v10,v21,v01,v12)&&!isAny4(bamboo_jungle,v10,v21,v01,v12)) out[i+j*w]=v11;
                        else out[i+j*w]=jungle_edge;
                    } else out[i+j*w]=plains;
                } else out[i+j*w]=v11;
            }
        }
    }
    return 0;
}

// ----- 2nd half: climate / river / ocean (1.16 path) -------------------------
// Reuse cubiomes' pure id-helpers (getMutated/areSimilar/isDeepOcean/isOceanic/
// getCategory/isSnowy/isMesa) + samplePerlin in the harness — all switch-tables or
// pure float math, easy to port to __device__ later. g.oceanRnd table -> GPU upload.

GPUDEV static int Gm_hills(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;       // biome chain
    int *riv=out+pW*pH;
    err=G_genArea(_gl,_pn,l->p2,riv,pX,pZ,pW,pH); if(err)return err;          // river chain
    int mc=l->mc; uint64_t st=l->startSalt, ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int a11=out[i+1+(j+1)*pW]; int b11=riv[i+1+(j+1)*pW];
        int64_t idx=i+j*w; int bn=-1;
        if(mc>=MC_1_7) bn=(b11-2)%29;
        if(bn==1 && b11>=2 && !D_isShallowOcean(a11)){
            int m=D_getMutated(mc,a11); out[idx]=(m>0)?m:a11;
        } else {
            cs=G_chunkSeed(ss,i+x,j+z);
            if(bn==0 || G_firstIsZero(cs,3)){
                int hillID=a11;
                switch(a11){
                case desert: hillID=desert_hills; break;
                case forest: hillID=wooded_hills; break;
                case birch_forest: hillID=birch_forest_hills; break;
                case dark_forest: hillID=plains; break;
                case taiga: hillID=taiga_hills; break;
                case giant_tree_taiga: hillID=giant_tree_taiga_hills; break;
                case snowy_taiga: hillID=snowy_taiga_hills; break;
                case plains:
                    cs=G_step(cs,st); hillID=G_firstIsZero(cs,3)?wooded_hills:forest; break;
                case snowy_tundra: hillID=snowy_mountains; break;
                case jungle: hillID=jungle_hills; break;
                case bamboo_jungle: hillID=bamboo_jungle_hills; break;
                case ocean: hillID=deep_ocean; break;          // mc>=1.7
                case mountains: hillID=wooded_mountains; break;// mc>=1.7
                case savanna: hillID=savanna_plateau; break;
                default:
                    if(D_areSimilar(mc,a11,wooded_badlands_plateau)) hillID=badlands;
                    else if(D_isDeepOcean(a11)){
                        cs=G_step(cs,st);
                        if(G_firstIsZero(cs,3)){ cs=G_step(cs,st); hillID=G_firstIsZero(cs,2)?plains:forest; }
                    }
                    break;
                }
                if(bn==0 && hillID!=a11){ hillID=D_getMutated(mc,hillID); if(hillID<0) hillID=a11; }
                if(hillID!=a11){
                    int a10=out[i+1+(j+0)*pW],a21=out[i+2+(j+1)*pW],a01=out[i+0+(j+1)*pW],a12=out[i+1+(j+2)*pW];
                    int eq=0;
                    if(D_areSimilar(mc,a10,a11))eq++; if(D_areSimilar(mc,a21,a11))eq++;
                    if(D_areSimilar(mc,a01,a11))eq++; if(D_areSimilar(mc,a12,a11))eq++;
                    out[idx]=(eq>=3)?hillID:a11;   // mc>1.6 -> 3+0
                } else out[idx]=a11;
            } else out[idx]=a11;
        }
    }
    return 0;
}

GPUDEV static inline int G_reduceID(int id){ return id>=2 ? 2+(id&1) : id; }
GPUDEV static int Gm_river(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    for(j=0;j<h;j++){
        int *vz0=out+(j+0)*pW,*vz1=out+(j+1)*pW,*vz2=out+(j+2)*pW;
        for(i=0;i<w;i++){
            int v01=G_reduceID(vz1[i+0]),v11=G_reduceID(vz1[i+1]),v21=G_reduceID(vz1[i+2]);
            int v10=G_reduceID(vz0[i+1]),v12=G_reduceID(vz2[i+1]);
            out[i+j*w]=(v11==v01&&v11==v10&&v11==v12&&v11==v21)?-1:river;
        }
    }
    return 0;
}

GPUDEV static int Gm_smooth(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++){
        int *vz0=out+(j+0)*pW,*vz1=out+(j+1)*pW,*vz2=out+(j+2)*pW;
        for(i=0;i<w;i++){
            int v11=vz1[i+1],v01=vz1[i+0],v10=vz0[i+1];
            if(v11!=v01||v11!=v10){
                int v21=vz1[i+2],v12=vz2[i+1];
                if(v01==v21&&v10==v12){ cs=G_chunkSeed(ss,i+x,j+z); v11=(cs&(1ULL<<24))?v10:v01; }
                else { if(v01==v21)v11=v01; if(v10==v12)v11=v10; }
            }
            out[i+j*w]=v11;
        }
    }
    return 0;
}

GPUDEV static int Gm_sunflower(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j; int err=G_genArea(_gl,_pn,l->p,out,x,z,w,h); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++)
        if(out[i+j*w]==plains){ cs=G_chunkSeed(ss,i+x,j+z); if(G_firstIsZero(cs,57)) out[i+j*w]=sunflower_plains; }
    return 0;
}

GPUDEV static inline int G_replaceOcean(int *out,int idx,int v10,int v21,int v01,int v12,int id,int repl){
    if(D_isOceanic(id))return 0;
    out[idx]=(D_isOceanic(v10)||D_isOceanic(v21)||D_isOceanic(v01)||D_isOceanic(v12))?repl:id;
    return 1;
}
GPUDEV static inline int G_isAll4JFTO(int mc,int a,int b,int c,int d){
    return (D_getCategory(mc,a)==jungle||a==forest||a==taiga||D_isOceanic(a))&&
           (D_getCategory(mc,b)==jungle||b==forest||b==taiga||D_isOceanic(b))&&
           (D_getCategory(mc,c)==jungle||c==forest||c==taiga||D_isOceanic(c))&&
           (D_getCategory(mc,d)==jungle||d==forest||d==taiga||D_isOceanic(d));
}
GPUDEV static inline int G_isAny4Oc(int a,int b,int c,int d){ return D_isOceanic(a)||D_isOceanic(b)||D_isOceanic(c)||D_isOceanic(d); }
GPUDEV static int Gm_shore(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j; int mc=l->mc;
    int err=G_genArea(_gl,_pn,l->p,out,pX,pZ,pW,pH); if(err)return err;
    for(j=0;j<h;j++){
        int *vz0=out+(j+0)*pW,*vz1=out+(j+1)*pW,*vz2=out+(j+2)*pW;
        for(i=0;i<w;i++){
            int v11=vz1[i+1],v10=vz0[i+1],v21=vz1[i+2],v01=vz1[i+0],v12=vz2[i+1];
            if(v11==mushroom_fields){
                out[i+j*w]=isAny4(ocean,v10,v21,v01,v12)?mushroom_field_shore:v11; continue;
            }
            if(D_getCategory(mc,v11)==jungle){
                if(G_isAll4JFTO(mc,v10,v21,v01,v12)) out[i+j*w]=G_isAny4Oc(v10,v21,v01,v12)?beach:v11;
                else out[i+j*w]=jungle_edge;
            } else if(v11==mountains||v11==wooded_mountains){
                G_replaceOcean(out,i+j*w,v10,v21,v01,v12,v11,stone_shore);
            } else if(D_isSnowy(v11)){
                G_replaceOcean(out,i+j*w,v10,v21,v01,v12,v11,snowy_beach);
            } else if(v11==badlands||v11==wooded_badlands_plateau){
                if(!G_isAny4Oc(v10,v21,v01,v12))
                    out[i+j*w]=(D_isMesa(v10)&&D_isMesa(v21)&&D_isMesa(v01)&&D_isMesa(v12))?v11:desert;
                else out[i+j*w]=v11;
            } else {
                if(v11!=ocean&&v11!=deep_ocean&&v11!=river&&v11!=swamp)
                    out[i+j*w]=G_isAny4Oc(v10,v21,v01,v12)?beach:v11;
                else out[i+j*w]=v11;
            }
        }
    }
    return 0;
}
GPUDEV static int Gm_riverMix(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int err=G_genArea(_gl,_pn,l->p,out,x,z,w,h); if(err)return err;
    int64_t len=w*(int64_t)h, idx; int *buf=out+len;
    err=G_genArea(_gl,_pn,l->p2,buf,x,z,w,h); if(err)return err;
    for(idx=0;idx<len;idx++){
        int v=out[idx];
        if(buf[idx]==river && v!=ocean && !D_isOceanic(v)){
            if(v==snowy_tundra) v=frozen_river;
            else if(v==mushroom_fields||v==mushroom_field_shore) v=mushroom_field_shore;
            else v=river;
        }
        out[idx]=v;
    }
    return 0;
}
GPUDEV static int Gm_oceanTemp(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j; const PerlinNoiseG *rnd=_pn;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        double tmp=D_samplePerlin(rnd,(i+x)/8.0,(j+z)/8.0,0,0,0);
        if(tmp>0.4) out[i+j*w]=warm_ocean;
        else if(tmp>0.2) out[i+j*w]=lukewarm_ocean;
        else if(tmp<-0.4) out[i+j*w]=frozen_ocean;
        else if(tmp<-0.2) out[i+j*w]=cold_ocean;
        else out[i+j*w]=ocean;
    }
    return 0;
}
GPUDEV static int Gm_oceanMix(const LayerG* _gl, const PerlinNoiseG* _pn, const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j; int lx0,lx1,lz0,lz1,lw,lh;
    int err=G_genArea(_gl,_pn,l->p2,out,x,z,w,h); if(err)return err;
    lx0=0;lx1=w;lz0=0;lz1=h;
    for(j=0;j<h;j++){ int jc=(j-8>0&&j+9<h);
        for(i=0;i<w;i++){
            if(jc&&i-8>0&&i+9<w)continue;
            int oid=out[i+j*w];
            if(oid==warm_ocean||oid==frozen_ocean){
                if(i-8<lx0)lx0=i-8; if(i+9>lx1)lx1=i+9; if(j-8<lz0)lz0=j-8; if(j+9>lz1)lz1=j+9;
            }
        }
    }
    int *land=out+w*h; lw=lx1-lx0; lh=lz1-lz0;
    err=G_genArea(_gl,_pn,l->p,land,x+lx0,z+lz0,lw,lh); if(err)return err;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int landID=land[(i-lx0)+(j-lz0)*lw]; int oid=out[i+j*w]; int repl=0,ii,jj;
        if(!D_isOceanic(landID)){ out[i+j*w]=landID; continue; }
        if(oid==warm_ocean) repl=lukewarm_ocean;
        if(oid==frozen_ocean) repl=cold_ocean;
        if(repl){
            for(ii=-8;ii<=8;ii+=4) for(jj=-8;jj<=8;jj+=4){
                int id=land[(i+ii-lx0)+(j+jj-lz0)*lw];
                if(!D_isOceanic(id)){ out[i+j*w]=repl; goto loop_x; }
            }
        }
        if(landID==deep_ocean){
            switch(oid){
            case lukewarm_ocean: oid=deep_lukewarm_ocean; break;
            case ocean: oid=deep_ocean; break;
            case cold_ocean: oid=deep_cold_ocean; break;
            case frozen_ocean: oid=deep_frozen_ocean; break;
            }
        }
        out[i+j*w]=oid;
        loop_x:;
    }
    return 0;
}
GPUDEV static int G_genArea(const LayerG* _gl, const PerlinNoiseG* _pn, int li, int *out, int x, int z, int w, int h){
    const LayerG *l=&_gl[li];
    switch(l->type){
        case G_CONTINENT:  return Gm_continent(_gl,_pn,l,out,x,z,w,h);
        case G_ZOOM_FUZZY: return Gm_zoomFuzzy(_gl,_pn,l,out,x,z,w,h);
        case G_ZOOM:       return Gm_zoom(_gl,_pn,l,out,x,z,w,h);
        case G_LAND:       return Gm_land(_gl,_pn,l,out,x,z,w,h);
        case G_ISLAND:     return Gm_island(_gl,_pn,l,out,x,z,w,h);
        case G_SNOW:       return Gm_snow(_gl,_pn,l,out,x,z,w,h);
        case G_COOL:       return Gm_cool(_gl,_pn,l,out,x,z,w,h);
        case G_HEAT:       return Gm_heat(_gl,_pn,l,out,x,z,w,h);
        case G_SPECIAL:    return Gm_special(_gl,_pn,l,out,x,z,w,h);
        case G_MUSHROOM:   return Gm_mushroom(_gl,_pn,l,out,x,z,w,h);
        case G_DEEPOCEAN:  return Gm_deepOcean(_gl,_pn,l,out,x,z,w,h);
        case G_BIOME:      return Gm_biome(_gl,_pn,l,out,x,z,w,h);
        case G_NOISE:      return Gm_noise(_gl,_pn,l,out,x,z,w,h);
        case G_BAMBOO:     return Gm_bamboo(_gl,_pn,l,out,x,z,w,h);
        case G_BIOMEEDGE:  return Gm_biomeEdge(_gl,_pn,l,out,x,z,w,h);
        case G_HILLS:      return Gm_hills(_gl,_pn,l,out,x,z,w,h);
        case G_RIVER:      return Gm_river(_gl,_pn,l,out,x,z,w,h);
        case G_SMOOTH:     return Gm_smooth(_gl,_pn,l,out,x,z,w,h);
        case G_SUNFLOWER:  return Gm_sunflower(_gl,_pn,l,out,x,z,w,h);
        case G_SHORE:      return Gm_shore(_gl,_pn,l,out,x,z,w,h);
        case G_RIVERMIX:   return Gm_riverMix(_gl,_pn,l,out,x,z,w,h);
        case G_OCEANTEMP:  return Gm_oceanTemp(_gl,_pn,l,out,x,z,w,h);
        case G_OCEANMIX:   return Gm_oceanMix(_gl,_pn,l,out,x,z,w,h);
        default: return 1;
    }
}

// ===================== Java RNG + per-thread ctx + stronghold snap ===========
#ifndef PI
#define PI 3.14159265358979323846
#endif
GPUDEV static inline void J_setSeed(uint64_t*s,uint64_t v){ *s=(v^0x5deece66dULL)&((1ULL<<48)-1); }
GPUDEV static inline int J_next(uint64_t*s,int bits){ *s=(*s*0x5deece66dULL+0xbULL)&((1ULL<<48)-1); return (int)((int64_t)*s>>(48-bits)); }
GPUDEV static inline int J_nextInt(uint64_t*s,int n){
    int bits,val; const int m=n-1;
    if((m&n)==0){ uint64_t x=n*(uint64_t)J_next(s,31); return (int)((int64_t)x>>31); }
    do{ bits=J_next(s,31); val=bits%n; }while((int32_t)((uint32_t)bits-val+m)<0);
    return val;
}
GPUDEV static inline double J_nextDouble(uint64_t*s){ uint64_t x=(uint64_t)J_next(s,26); x<<=27; x+=J_next(s,27); return (int64_t)x/(double)(1ULL<<53); }
GPUDEV static inline int S_idMatch(int id,uint64_t b,uint64_t m){ return id<128?(int)((b>>id)&1):(int)((m>>(id-128))&1); }

// port of cubiomes perlinInit (ocean temp noise), seeded from the world seed
GPUDEV static void D_perlinInit(PerlinNoiseG* noise, uint64_t* seed){
    noise->a=J_nextDouble(seed)*256.0; noise->b=J_nextDouble(seed)*256.0; noise->c=J_nextDouble(seed)*256.0;
    noise->amplitude=1.0; noise->lacunarity=1.0;
    uint8_t* idx=noise->d;
    for(int i=0;i<256;i++) idx[i]=(uint8_t)i;
    for(int i=0;i<256;i++){ int j=J_nextInt(seed,256-i)+i; uint8_t n=idx[i]; idx[i]=idx[j]; idx[j]=n; }
    idx[256]=idx[0];
    double i2=floor(noise->b), d2=noise->b-i2;
    noise->h2=(uint8_t)(int)i2; noise->d2=d2; noise->t2=d2*d2*d2*(d2*(d2*6.0-15.0)+10.0);
}
// build the per-thread layer table (derive startSalt/startSeed from ws+layerSalt) + Perlin
GPUDEV static void G_deriveCtx(uint64_t ws, LayerG* gl, PerlinNoiseG* pn){
    for(int i=0;i<GL_MAX;i++){
        gl[i]=GL_TMPL[i];
        uint64_t ls=GL_TMPL[i].layerSalt;
        if(ls==0){ gl[i].startSalt=0; gl[i].startSeed=0; }
        else { uint64_t st=ws; st=G_step(st,ls); st=G_step(st,ls); st=G_step(st,ls);
               gl[i].startSalt=st; gl[i].startSeed=G_step(st,0); }
    }
    uint64_t s; J_setSeed(&s, ws); D_perlinInit(pn, &s);
}
// locateBiome (1.16). gen = biome scratch (>= 49000 ints).
GPUDEV static void S_locateBiome(const LayerG* _gl, const PerlinNoiseG* _pn,
                                 int x,int z,int radius,uint64_t vB,uint64_t vM,
                                 uint64_t*rng,int*gen,int*ox,int*oz){
    int x1=(x-radius)>>2, z1=(z-radius)>>2, x2=(x+radius)>>2, z2=(z+radius)>>2;
    int width=x2-x1+1, height=z2-z1+1;
    G_genArea(_gl,_pn,GL_OCEANMIX, gen, x1, z1, width, height);
    int rx=x, rz=z, found=0, j=2;
    for(int i=0;i<width*height;i++){
        if(!S_idMatch(gen[i],vB,vM))continue;
        if(found==0 || J_nextInt(rng,j++)==0){ rx=(x1+i%width)*4; rz=(z1+i/width)*4; found=1; }
    }
    *ox=rx; *oz=rz;
}
// 128-stronghold snap for ONE seed. scratch layout: [LayerG gl[GL_MAX]][PerlinNoiseG pn][int gen[~60000]]
// scratch must be >= GL_MAX*sizeof(LayerG)+sizeof(PerlinNoiseG)+60000*sizeof(int) bytes.
GPUDEV static void S_strongholds128(int mc, uint64_t seed, uint64_t vB, uint64_t vM,
                                    void *scratch, int *out_x, int *out_z){
    LayerG* gl = (LayerG*)scratch;
    PerlinNoiseG* pn = (PerlinNoiseG*)(gl + GL_MAX);
    int* gen = (int*)(pn + 1);
    G_deriveCtx(seed, gl, pn);                 // per-seed layer seeds + ocean Perlin (full 64-bit seed)
    uint64_t s48=seed&((1ULL<<48)-1);
    uint64_t rnds; J_setSeed(&rnds,s48);
    double angle=2.0*PI*J_nextDouble(&rnds);
    double dist=(4.0*32.0)+(J_nextDouble(&rnds)-0.5)*32*2.5;   // mc>=1.9
    int index=0,ringnum=0,ringmax=3,ringidx=0;
    int nax=((int)round(cos(angle)*dist)*16)+8, naz=((int)round(sin(angle)*dist)*16)+8;
    for(int k=0;k<128;k++){
        int px,pz; S_locateBiome(gl,pn,nax,naz,112,vB,vM,&rnds,gen,&px,&pz);
        px=(px&~15)+4; pz=(pz&~15)+4;
        out_x[k]=px; out_z[k]=pz;
        ringidx++;
        angle += 2*PI/ringmax;
        if(ringidx==ringmax){
            ringnum++; ringidx=0;
            ringmax = ringmax + 2*ringmax/(ringnum+1);
            if(ringmax > 128-index) ringmax = 128-index;
            angle += J_nextDouble(&rnds)*PI*2.0;
        }
        dist=(4.0*32.0)+(6.0*ringnum*32.0)+(J_nextDouble(&rnds)-0.5)*32*2.5;
        nax=((int)round(cos(angle)*dist)*16)+8; naz=((int)round(sin(angle)*dist)*16)+8;
        index++;
    }
}
#endif // BIOME_GPU_KERNEL_CUH
