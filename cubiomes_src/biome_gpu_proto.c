// biome_gpu_proto.c — pure-C prototype of the 1.16 (MC_1_16) biome layer stack,
// structured so every map function is a self-contained, GPU-portable routine that
// recurses through parents *by index* into a flat LayerG table (no pointers), using
// a single per-thread scratch buffer exactly like cubiomes (parent area at out[0..],
// result written back into out[0..w*h]).
//
// GOAL: port the cubiomes 1.16 layer stack to CUDA to accelerate the biome snap
// (the bottleneck of the 128-stronghold mode). This file is the CPU de-risking
// stage: every layer is validated BIT-EXACT against cubiomes' own getMap in the
// sandbox before being translated to __device__ code.
//
// Build / validate (Linux sandbox, against cubiomes sources):
//   gcc -O2 -I. biome_gpu_proto.c noise.c biomes.c layers.c biomenoise.c \
//       generator.c util.c -lm -o proto && ./proto
//
// id-classification helpers (isShallowOcean/isOceanic/getMutated/areSimilar) are
// pure functions of a biome id — reused from cubiomes here, trivial switch-tables
// to port to __device__ later. The hard part (RNG + scratch + recursion) is here.
//
// STATUS: validated bit-exact up to L_BIOME_EDGE_64 (the full biome-assignment
// sub-chain). TODO for full scale-4: mapHills(2p)+getMutated, mapSunflower,
// mapShore, mapSmooth, mapRiver, mapRiverMix(2p), mapOceanTemp(perlin),
// mapOceanMix(2p), then mapVoronoi for scale 1.

#include "generator.h"
#include "biomes.h"
#include "rng.h"
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

// ===================== RNG primitives (pure arithmetic -> __device__) =========
static inline uint64_t G_step(uint64_t s, uint64_t salt){
    return s*(s*6364136223846793005ULL+1442695040888963407ULL)+salt;
}
static inline int G_firstIsZero(uint64_t s, int mod){
    return (int)(((int64_t)s>>24)%mod)==0;
}
static inline int G_firstInt(uint64_t s, int mod){
    int r=(int)(((int64_t)s>>24)%mod); if(r<0)r+=mod; return r;
}
static inline uint64_t G_chunkSeed(uint64_t ss,int x,int z){
    uint64_t cs=ss+x; cs=G_step(cs,z); cs=G_step(cs,x); cs=G_step(cs,z); return cs;
}
static inline int isAny4(int id,int a,int b,int c,int d){ return id==a||id==b||id==c||id==d; }

// ===================== flat layer table (GPU-uploadable) ======================
enum {
    G_CONTINENT=1, G_ZOOM_FUZZY, G_ZOOM, G_LAND, G_ISLAND, G_SNOW, G_COOL,
    G_HEAT, G_SPECIAL, G_MUSHROOM, G_DEEPOCEAN, G_BIOME, G_NOISE, G_BAMBOO,
    G_BIOMEEDGE, G_HILLS, G_RIVER, G_SMOOTH, G_SUNFLOWER, G_SHORE,
    G_RIVERMIX, G_OCEANTEMP, G_OCEANMIX
};
typedef struct {
    int type; int mc; int zoom, edge;
    uint64_t startSalt, startSeed; int p, p2;
} LayerG;

#define MAXLAY 128
static LayerG GL[MAXLAY];

static int G_genArea(int li, int *out, int x, int z, int w, int h); // fwd

// biome-selection tables (1.16) — copied from layers.c
static const int G_warm[] = {desert, desert, desert, savanna, savanna, plains};
static const int G_lush[] = {forest, dark_forest, mountains, plains, birch_forest, swamp};
static const int G_cold[] = {forest, mountains, taiga, plains};
static const int G_snow[] = {snowy_tundra, snowy_tundra, snowy_tundra, snowy_taiga};

// ===================== ported map functions ===================================
static int Gm_continent(const LayerG *l, int *out, int x, int z, int w, int h){
    uint64_t ss=l->startSeed;
    for(int j=0;j<h;j++) for(int i=0;i<w;i++)
        out[j*w+i]=G_firstIsZero(G_chunkSeed(ss,i+x,j+z),10);
    if(x>-w&&x<=0&&z>-h&&z<=0) out[-z*(int64_t)w-x]=1;
    return 0;
}

static int Gm_zoomFuzzy(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x>>1, pZ=z>>1;
    int64_t pW=((x+w)>>1)-pX+1, pH=((z+h)>>1)-pZ+1, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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
    for(j=0;j<h;j++) memmove(&out[j*w],&buf[(j+(z&1))*newW+(x&1)],w*sizeof(int));
    return 0;
}

static inline int G_select4(uint32_t cs,uint32_t st,int v00,int v01,int v10,int v11){
    int cv00=(v00==v10)+(v00==v01)+(v00==v11), cv10=(v10==v01)+(v10==v11), cv01=(v01==v11);
    if(cv00>cv10&&cv00>cv01)return v00; else if(cv10>cv00)return v10; else if(cv01>cv00)return v01;
    cs*=cs*1284865837+4150755663; cs+=st; int r=(cs>>24)&3; return r==0?v00:r==1?v10:r==2?v01:v11;
}
static int Gm_zoom(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x>>1, pZ=z>>1;
    int64_t pW=((x+w)>>1)-pX+1, pH=((z+h)>>1)-pZ+1, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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
    for(j=0;j<h;j++) memmove(&out[j*w],&buf[(j+(z&1))*newW+(x&1)],w*sizeof(int));
    return 0;
}

static int Gm_land(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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

static int Gm_island(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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

static int Gm_snow(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v11=out[i+1+(j+1)*pW];
        if(!isShallowOcean(v11)){
            cs=G_chunkSeed(ss,i+x,j+z); int r=G_firstInt(cs,6);
            if(r==0)v11=Freezing; else if(r<=1)v11=Cold; else v11=Warm;
        }
        out[i+j*w]=v11;
    }
    return 0;
}

static int Gm_cool(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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

static int Gm_heat(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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

static int Gm_special(const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j;
    int err=G_genArea(l->p,out,x,z,w,h); if(err)return err;
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

static int Gm_mushroom(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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

static int Gm_deepOcean(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int v11=out[(i+1)+(j+1)*pW];
        if(isShallowOcean(v11)){
            int oc=0;
            if(isShallowOcean(out[(i+1)+(j+0)*pW]))oc++;
            if(isShallowOcean(out[(i+2)+(j+1)*pW]))oc++;
            if(isShallowOcean(out[(i+0)+(j+1)*pW]))oc++;
            if(isShallowOcean(out[(i+1)+(j+2)*pW]))oc++;
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

static int Gm_biome(const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j;
    int err=G_genArea(l->p,out,x,z,w,h); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    // 1.16 path (mc > MC_1_6): isOceanic / mushroom shortcut + Warm/Lush/Cold/Freezing
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int64_t idx=i+j*w; int id=out[idx]; int v;
        int hasHighBit=(id&0xf00); id&=~0xf00;
        if(isOceanic(id)||id==mushroom_fields){ out[idx]=id; continue; }
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

static int Gm_noise(const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j;
    int err=G_genArea(l->p,out,x,z,w,h); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    int mod=299999; // mc > MC_1_6
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        if(out[i+j*w]>0){ cs=G_chunkSeed(ss,i+x,j+z); out[i+j*w]=G_firstInt(cs,mod)+2; }
        else out[i+j*w]=0;
    }
    return 0;
}

static int Gm_bamboo(const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j;
    int err=G_genArea(l->p,out,x,z,w,h); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int64_t idx=i+j*w; if(out[idx]!=jungle)continue;
        cs=G_chunkSeed(ss,i+x,j+z); if(G_firstIsZero(cs,10)) out[idx]=bamboo_jungle;
    }
    return 0;
}

static inline int G_replaceEdge(int *out,int idx,int mc,int v10,int v21,int v01,int v12,int id,int baseID,int edgeID){
    if(id!=baseID)return 0;
    if(areSimilar(mc,v10,baseID)&&areSimilar(mc,v21,baseID)&&areSimilar(mc,v01,baseID)&&areSimilar(mc,v12,baseID))
        out[idx]=id;
    else out[idx]=edgeID;
    return 1;
}
static int Gm_biomeEdge(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j; int mc=l->mc;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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
static const void *G_oceanRnd = 0;  // set during extraction (L_OCEAN_TEMP_256.noise)

static int Gm_hills(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;       // biome chain
    int *riv=out+pW*pH;
    err=G_genArea(l->p2,riv,pX,pZ,pW,pH); if(err)return err;          // river chain
    int mc=l->mc; uint64_t st=l->startSalt, ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int a11=out[i+1+(j+1)*pW]; int b11=riv[i+1+(j+1)*pW];
        int64_t idx=i+j*w; int bn=-1;
        if(mc>=MC_1_7) bn=(b11-2)%29;
        if(bn==1 && b11>=2 && !isShallowOcean(a11)){
            int m=getMutated(mc,a11); out[idx]=(m>0)?m:a11;
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
                    if(areSimilar(mc,a11,wooded_badlands_plateau)) hillID=badlands;
                    else if(isDeepOcean(a11)){
                        cs=G_step(cs,st);
                        if(G_firstIsZero(cs,3)){ cs=G_step(cs,st); hillID=G_firstIsZero(cs,2)?plains:forest; }
                    }
                    break;
                }
                if(bn==0 && hillID!=a11){ hillID=getMutated(mc,hillID); if(hillID<0) hillID=a11; }
                if(hillID!=a11){
                    int a10=out[i+1+(j+0)*pW],a21=out[i+2+(j+1)*pW],a01=out[i+0+(j+1)*pW],a12=out[i+1+(j+2)*pW];
                    int eq=0;
                    if(areSimilar(mc,a10,a11))eq++; if(areSimilar(mc,a21,a11))eq++;
                    if(areSimilar(mc,a01,a11))eq++; if(areSimilar(mc,a12,a11))eq++;
                    out[idx]=(eq>=3)?hillID:a11;   // mc>1.6 -> 3+0
                } else out[idx]=a11;
            } else out[idx]=a11;
        }
    }
    return 0;
}

static inline int G_reduceID(int id){ return id>=2 ? 2+(id&1) : id; }
static int Gm_river(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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

static int Gm_smooth(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
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

static int Gm_sunflower(const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j; int err=G_genArea(l->p,out,x,z,w,h); if(err)return err;
    uint64_t ss=l->startSeed, cs;
    for(j=0;j<h;j++) for(i=0;i<w;i++)
        if(out[i+j*w]==plains){ cs=G_chunkSeed(ss,i+x,j+z); if(G_firstIsZero(cs,57)) out[i+j*w]=sunflower_plains; }
    return 0;
}

static inline int G_replaceOcean(int *out,int idx,int v10,int v21,int v01,int v12,int id,int repl){
    if(isOceanic(id))return 0;
    out[idx]=(isOceanic(v10)||isOceanic(v21)||isOceanic(v01)||isOceanic(v12))?repl:id;
    return 1;
}
static inline int G_isAll4JFTO(int mc,int a,int b,int c,int d){
    return (getCategory(mc,a)==jungle||a==forest||a==taiga||isOceanic(a))&&
           (getCategory(mc,b)==jungle||b==forest||b==taiga||isOceanic(b))&&
           (getCategory(mc,c)==jungle||c==forest||c==taiga||isOceanic(c))&&
           (getCategory(mc,d)==jungle||d==forest||d==taiga||isOceanic(d));
}
static inline int G_isAny4Oc(int a,int b,int c,int d){ return isOceanic(a)||isOceanic(b)||isOceanic(c)||isOceanic(d); }
static int Gm_shore(const LayerG *l, int *out, int x, int z, int w, int h){
    int pX=x-1, pZ=z-1; int64_t pW=w+2, pH=h+2, i,j; int mc=l->mc;
    int err=G_genArea(l->p,out,pX,pZ,pW,pH); if(err)return err;
    for(j=0;j<h;j++){
        int *vz0=out+(j+0)*pW,*vz1=out+(j+1)*pW,*vz2=out+(j+2)*pW;
        for(i=0;i<w;i++){
            int v11=vz1[i+1],v10=vz0[i+1],v21=vz1[i+2],v01=vz1[i+0],v12=vz2[i+1];
            if(v11==mushroom_fields){
                out[i+j*w]=isAny4(ocean,v10,v21,v01,v12)?mushroom_field_shore:v11; continue;
            }
            if(getCategory(mc,v11)==jungle){
                if(G_isAll4JFTO(mc,v10,v21,v01,v12)) out[i+j*w]=G_isAny4Oc(v10,v21,v01,v12)?beach:v11;
                else out[i+j*w]=jungle_edge;
            } else if(v11==mountains||v11==wooded_mountains){
                G_replaceOcean(out,i+j*w,v10,v21,v01,v12,v11,stone_shore);
            } else if(isSnowy(v11)){
                G_replaceOcean(out,i+j*w,v10,v21,v01,v12,v11,snowy_beach);
            } else if(v11==badlands||v11==wooded_badlands_plateau){
                if(!G_isAny4Oc(v10,v21,v01,v12))
                    out[i+j*w]=(isMesa(v10)&&isMesa(v21)&&isMesa(v01)&&isMesa(v12))?v11:desert;
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

static int Gm_riverMix(const LayerG *l, int *out, int x, int z, int w, int h){
    int err=G_genArea(l->p,out,x,z,w,h); if(err)return err;       // biome chain
    int64_t len=w*(int64_t)h, idx; int *buf=out+len;
    err=G_genArea(l->p2,buf,x,z,w,h); if(err)return err;          // river chain
    for(idx=0;idx<len;idx++){
        int v=out[idx];
        if(buf[idx]==river && v!=ocean && !isOceanic(v)){   // mc>1.6
            if(v==snowy_tundra) v=frozen_river;
            else if(v==mushroom_fields||v==mushroom_field_shore) v=mushroom_field_shore;
            else v=river;
        }
        out[idx]=v;
    }
    return 0;
}

static int Gm_oceanTemp(const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j; const PerlinNoise *rnd=(const PerlinNoise*)G_oceanRnd;
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        double tmp=samplePerlin(rnd,(i+x)/8.0,(j+z)/8.0,0,0,0);
        if(tmp>0.4) out[i+j*w]=warm_ocean;
        else if(tmp>0.2) out[i+j*w]=lukewarm_ocean;
        else if(tmp<-0.4) out[i+j*w]=frozen_ocean;
        else if(tmp<-0.2) out[i+j*w]=cold_ocean;
        else out[i+j*w]=ocean;
    }
    return 0;
}

static int Gm_oceanMix(const LayerG *l, int *out, int x, int z, int w, int h){
    int64_t i,j; int lx0,lx1,lz0,lz1,lw,lh;
    int err=G_genArea(l->p2,out,x,z,w,h); if(err)return err;       // ocean chain into out
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
    err=G_genArea(l->p,land,x+lx0,z+lz0,lw,lh); if(err)return err; // land chain
    for(j=0;j<h;j++) for(i=0;i<w;i++){
        int landID=land[(i-lx0)+(j-lz0)*lw]; int oid=out[i+j*w]; int repl=0,ii,jj;
        if(!isOceanic(landID)){ out[i+j*w]=landID; continue; }
        if(oid==warm_ocean) repl=lukewarm_ocean;
        if(oid==frozen_ocean) repl=cold_ocean;
        if(repl){
            for(ii=-8;ii<=8;ii+=4) for(jj=-8;jj<=8;jj+=4){
                int id=land[(i+ii-lx0)+(j+jj-lz0)*lw];
                if(!isOceanic(id)){ out[i+j*w]=repl; goto loop_x; }
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

// ===================== dispatcher =============================================
static int G_genArea(int li, int *out, int x, int z, int w, int h){
    const LayerG *l=&GL[li];
    switch(l->type){
        case G_CONTINENT:  return Gm_continent(l,out,x,z,w,h);
        case G_ZOOM_FUZZY: return Gm_zoomFuzzy(l,out,x,z,w,h);
        case G_ZOOM:       return Gm_zoom(l,out,x,z,w,h);
        case G_LAND:       return Gm_land(l,out,x,z,w,h);
        case G_ISLAND:     return Gm_island(l,out,x,z,w,h);
        case G_SNOW:       return Gm_snow(l,out,x,z,w,h);
        case G_COOL:       return Gm_cool(l,out,x,z,w,h);
        case G_HEAT:       return Gm_heat(l,out,x,z,w,h);
        case G_SPECIAL:    return Gm_special(l,out,x,z,w,h);
        case G_MUSHROOM:   return Gm_mushroom(l,out,x,z,w,h);
        case G_DEEPOCEAN:  return Gm_deepOcean(l,out,x,z,w,h);
        case G_BIOME:      return Gm_biome(l,out,x,z,w,h);
        case G_NOISE:      return Gm_noise(l,out,x,z,w,h);
        case G_BAMBOO:     return Gm_bamboo(l,out,x,z,w,h);
        case G_BIOMEEDGE:  return Gm_biomeEdge(l,out,x,z,w,h);
        case G_HILLS:      return Gm_hills(l,out,x,z,w,h);
        case G_RIVER:      return Gm_river(l,out,x,z,w,h);
        case G_SMOOTH:     return Gm_smooth(l,out,x,z,w,h);
        case G_SUNFLOWER:  return Gm_sunflower(l,out,x,z,w,h);
        case G_SHORE:      return Gm_shore(l,out,x,z,w,h);
        case G_RIVERMIX:   return Gm_riverMix(l,out,x,z,w,h);
        case G_OCEANTEMP:  return Gm_oceanTemp(l,out,x,z,w,h);
        case G_OCEANMIX:   return Gm_oceanMix(l,out,x,z,w,h);
        default: return 1; // unported layer in this DAG
    }
}

// ===================== extraction from a live cubiomes generator ==============
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

static int typeOf(const Layer *L){
    void *f=(void*)L->getMap;
    if(f==(void*)mapContinent)return G_CONTINENT;
    if(f==(void*)mapZoomFuzzy)return G_ZOOM_FUZZY;
    if(f==(void*)mapZoom)return G_ZOOM;
    if(f==(void*)mapLand)return G_LAND;
    if(f==(void*)mapIsland)return G_ISLAND;
    if(f==(void*)mapSnow)return G_SNOW;
    if(f==(void*)mapCool)return G_COOL;
    if(f==(void*)mapHeat)return G_HEAT;
    if(f==(void*)mapSpecial)return G_SPECIAL;
    if(f==(void*)mapMushroom)return G_MUSHROOM;
    if(f==(void*)mapDeepOcean)return G_DEEPOCEAN;
    if(f==(void*)mapBiome)return G_BIOME;
    if(f==(void*)mapNoise)return G_NOISE;
    if(f==(void*)mapBamboo)return G_BAMBOO;
    if(f==(void*)mapBiomeEdge)return G_BIOMEEDGE;
    if(f==(void*)mapHills)return G_HILLS;
    if(f==(void*)mapRiver)return G_RIVER;
    if(f==(void*)mapSmooth)return G_SMOOTH;
    if(f==(void*)mapSunflower)return G_SUNFLOWER;
    if(f==(void*)mapShore)return G_SHORE;
    if(f==(void*)mapRiverMix)return G_RIVERMIX;
    if(f==(void*)mapOceanTemp)return G_OCEANTEMP;
    if(f==(void*)mapOceanMix)return G_OCEANMIX;
    return 0;
}
static void extract(const Generator *g, int li){
    const Layer *L=&g->ls.layers[li];
    GL[li].type=typeOf(L); GL[li].mc=L->mc;
    GL[li].zoom=L->zoom; GL[li].edge=L->edge;
    GL[li].startSalt=L->startSalt; GL[li].startSeed=L->startSeed;
    GL[li].p = L->p ? (int)(L->p - g->ls.layers) : -1;
    GL[li].p2= L->p2? (int)(L->p2- g->ls.layers) : -1;
    if(GL[li].type==G_OCEANTEMP) G_oceanRnd = L->noise; // Perlin table (g.oceanRnd)
}

// extract the whole parent DAG of `target`; returns 0 ok, -1 if an unported type
static int extractDAG(const Generator *g, int target, char *done){
    int stack[512], sp=0; stack[sp++]=target;
    while(sp){
        int li=stack[--sp]; if(done[li])continue; done[li]=1;
        extract(g, li);
        if(GL[li].type==0){ printf("  !! UNPORTED layer index %d (getMap unmapped)\n", li); return -1; }
        if(GL[li].p>=0) stack[sp++]=GL[li].p;
        if(GL[li].p2>=0) stack[sp++]=GL[li].p2;
    }
    return 0;
}

static int validateLayer(const Generator *g, int target, const char *name){
    char done[MAXLAY]={0};
    if(extractDAG(g, target, done)!=0){ printf("[%s] cannot validate (unported dep)\n",name); return -1; }
    const Layer *lt=&g->ls.layers[target];
    int W=28,H=28,x0=-11,z0=-7;
    size_t need=getMinLayerCacheSize(lt,W,H);
    int *ref=(int*)calloc(need,sizeof(int));
    int *mine=(int*)calloc(need,sizeof(int));
    lt->getMap((Layer*)lt, ref, x0,z0,W,H);
    G_genArea(target, mine, x0,z0,W,H);
    int mism=0; for(int i=0;i<W*H;i++) if(ref[i]!=mine[i]) mism++;
    printf("[%-14s] layer %2d : %d/%d match  %s\n", name, target, W*H-mism, W*H,
           mism==0?"OK bit-exact":"*** MISMATCH ***");
    free(ref); free(mine);
    return mism;
}

int main(){
    Generator g; setupGenerator(&g, MC_1_16, 0); applySeed(&g, DIM_OVERWORLD, 1000ULL);
    int total=0;
    total += validateLayer(&g, L_CONTINENT_4096, "continent");
    total += validateLayer(&g, L_ZOOM_2048,      "zoomFuzzy");
    total += validateLayer(&g, L_LAND_2048,       "land2048");
    total += validateLayer(&g, L_ZOOM_1024,       "zoom1024");
    total += validateLayer(&g, L_LAND_1024_D,     "land1024D");  // after island/snow too
    total += validateLayer(&g, L_ISLAND_1024,     "island");
    total += validateLayer(&g, L_SNOW_1024,       "snow");
    total += validateLayer(&g, L_COOL_1024,       "cool");
    total += validateLayer(&g, L_HEAT_1024,       "heat");
    total += validateLayer(&g, L_SPECIAL_1024,    "special");
    total += validateLayer(&g, L_MUSHROOM_256,    "mushroom");
    total += validateLayer(&g, L_DEEP_OCEAN_256,  "deepOcean");
    total += validateLayer(&g, L_BIOME_256,       "biome");
    total += validateLayer(&g, L_BAMBOO_256,      "bamboo");
    total += validateLayer(&g, L_BIOME_EDGE_64,   "biomeEdge");
    // ---- 2nd half: climate / river / ocean (down to the scale-4 entry) ----
    total += validateLayer(&g, L_HILLS_64,        "hills");
    total += validateLayer(&g, L_SUNFLOWER_64,    "sunflower");
    total += validateLayer(&g, L_LAND_32,         "land32");
    total += validateLayer(&g, L_SHORE_16,        "shore");
    total += validateLayer(&g, L_SMOOTH_4,        "smooth");
    total += validateLayer(&g, L_RIVER_4,         "river");
    total += validateLayer(&g, L_SMOOTH_4_RIVER,  "smoothRiver");
    total += validateLayer(&g, L_RIVER_MIX_4,     "riverMix");
    total += validateLayer(&g, L_OCEAN_TEMP_256,  "oceanTemp");
    total += validateLayer(&g, L_OCEAN_MIX_4,     "oceanMix(=scale4)");

    // ---- definitive cross-check: my chain vs cubiomes getBiomeAt(4,x,z) ----
    int gb_mism=0;
    int pts[][2] = {{0,0},{100,100},{-50,200},{1000,-1000},{12,34}};
    size_t one=getMinLayerCacheSize(&g.ls.layers[L_OCEAN_MIX_4],1,1);
    int *cell=(int*)calloc(one,sizeof(int));
    for(int k=0;k<5;k++){
        int X=pts[k][0], Z=pts[k][1];
        char d2[MAXLAY]={0}; extractDAG(&g, L_OCEAN_MIX_4, d2);
        G_genArea(L_OCEAN_MIX_4, cell, X, Z, 1, 1);
        int ref = getBiomeAt(&g, 4, X, 0, Z);
        if(cell[0]!=ref){ gb_mism++;
            printf("  getBiomeAt(4,%d,%d): mine=%d cubiomes=%d  *** \n", X, Z, cell[0], ref); }
    }
    free(cell);
    printf("[getBiomeAt(4)] 5 points : %d/5 match  %s\n", 5-gb_mism,
           gb_mism==0?"OK (biome(4,0,0)=169 expected)":"*** MISMATCH ***");
    total += gb_mism;

    printf("\n%s\n", total==0 ?
        "==== FULL 1.16 STACK BIT-EXACT DOWN TO SCALE 4 (getBiomeAt) ====" :
        "==== MISMATCH(ES) ABOVE ====");
    return total==0?0:1;
}
