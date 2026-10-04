#include "generator.h"
#include "finders.h"
#include "biomes.h"
#include "rng.h"
#include "biomenoise.h"
#include <stdlib.h>
#include <math.h>
#define EXPORT
static const int PSX[14]={0,6,9,8,8,10,5,9,14,10,12,11,11,16};
static const int PSY[14]={0,10,12,9,9,10,7,7,9,8,8,17,16,16};
static const int PSZ[14]={0,6,9,9,9,7,7,9,9,9,10,16,16,16};
// `large` = setupGenerator flags (LARGE_BIOMES or 0) of this context. Set with
// cb_set_large() right after cb_create(); every function below that sets up the
// generator uses it (was hard-wired to 0 -> Large Biomes worlds were searched
// as default worlds). Stored in the context so the existing exports keep their
// signature (ABI-compatible with the Python bindings of an older build).
typedef struct { Generator g; SurfaceNoise sn; int gen_mc; uint64_t surf_seed; int surf_ok; int large; } Ctx;
EXPORT void* cb_create(){ Ctx*c=calloc(1,sizeof(Ctx)); if(c)c->gen_mc=-1; return c; }
EXPORT void cb_free(void*p){ free(p); }
// Select the world type of the context: large != 0 -> LARGE_BIOMES. Forces the
// next call to re-run setupGenerator (the cached generator is version+flags).
EXPORT void cb_set_large(void*p,int large){
    Ctx*c=p; int f = large ? LARGE_BIOMES : 0;
    if(c->large != f){ c->large=f; c->gen_mc=-1; }
}
// Cached generator setup (version + world type). Returns nothing; applySeed is
// left to the caller.
static void cb_gen_setup(Ctx*c,int mc){
    if(c->gen_mc != mc){ setupGenerator(&c->g,mc,(uint32_t)c->large); c->gen_mc=mc; }
}
// FAST setup for the corrupted SCAN : setupGenerator is cached (version-only,
// not seed) and we DON'T init the surface noise (only needed for the ruined Y,
// which the search no longer uses — the perfect checker computes Y exactly).
EXPORT void cb_setup(void*p,int mc,uint64_t seed){
    Ctx*c=p;
    cb_gen_setup(c,mc);
    applySeed(&c->g,DIM_OVERWORLD,seed);
}
// 1er stronghold : anneau (sans biome) + snappé (avec biome)
EXPORT int cb_first_stronghold(void*p,int mc,uint64_t seed,int*ringx,int*ringz,int*snapx,int*snapz){
    Ctx*c=p; StrongholdIter sh;
    Pos r=initFirstStronghold(&sh,mc,seed);
    *ringx=r.x; *ringz=r.z;
    nextStronghold(&sh,&c->g);
    *snapx=sh.pos.x; *snapz=sh.pos.z;
    return 1;
}
// ALL strongholds of a seed (up to max_count, normally 128). Writes snapped
// (x,z) pairs into out_xz (2*count ints). Returns the count. Feeds the
// 128-strongholds search mode (the corrupted/MaxY/eye finders then check each).
EXPORT int cb_strongholds128(void*p,int mc,uint64_t seed,int*out_xz,int max_count){
    Ctx*c=p;
    cb_gen_setup(c,mc);
    applySeed(&c->g,DIM_OVERWORLD,seed);
    StrongholdIter sh;
    initFirstStronghold(&sh,mc,seed);
    int k=0;
    for(int i=0;i<max_count;i++){
        int rem=nextStronghold(&sh,&c->g);
        out_xz[2*k]=sh.pos.x; out_xz[2*k+1]=sh.pos.z; k++;
        if(rem<=0) break;
    }
    return k;
}
// First (biome-snapped) stronghold WORLD position for EACH seed of an array.
// applySeed + nextStronghold per seed is the shared dominant cost of every
// seedlist finder (eye / maxY / corrupted). Writes world (x,z) to out_x/out_z.
EXPORT int seedlist_first_stronghold(void*p,int mc,const int64_t*seeds,int n,
                                     int*out_x,int*out_z){
    Ctx*c=p;
    cb_gen_setup(c,mc);
    for(int i=0;i<n;i++){
        uint64_t seed=(uint64_t)seeds[i];
        applySeed(&c->g,DIM_OVERWORLD,seed);
        StrongholdIter sh;
        initFirstStronghold(&sh,mc,seed);
        nextStronghold(&sh,&c->g);
        out_x[i]=sh.pos.x; out_z[i]=sh.pos.z;
    }
    return n;
}
// ALL strongholds (up to 128) of EACH seed in an array, FLATTENED. For each seed
// it writes one (seed, x, z) triple per stronghold. Returns the total written
// (capped at max_out). Feeds the "scan all 128" eye/maxY seedlist finders (each
// stronghold then gets the native eye/maxY metric + an exact Python validation).
EXPORT int cb_strongholds128_arr(void*p,int mc,const int64_t*seeds,int n,
                                 uint64_t*out_seed,int*out_x,int*out_z,int max_out){
    Ctx*c=p;
    cb_gen_setup(c,mc);
    int k=0;
    for(int i=0;i<n;i++){
        uint64_t s=(uint64_t)seeds[i];
        applySeed(&c->g,DIM_OVERWORLD,s);
        StrongholdIter sh; initFirstStronghold(&sh,mc,s);
        for(int si=0;si<128;si++){
            int rem=nextStronghold(&sh,&c->g);
            if(k<max_out){ out_seed[k]=s; out_x[k]=sh.pos.x; out_z[k]=sh.pos.z; }
            k++;
            if(rem<=0) break;
        }
    }
    return k;
}
// The FIRST per_seed strongholds of EACH seed (per_seed = 3: the first ring;
// 128: all, same as cb_strongholds128_arr). Flattened (seed, x, z). 03/10.
EXPORT int cb_strongholds_n_arr(void*p,int mc,const int64_t*seeds,int n,int per_seed,
                                uint64_t*out_seed,int*out_x,int*out_z,int max_out){
    Ctx*c=p;
    cb_gen_setup(c,mc);
    int k=0;
    if(per_seed<1) per_seed=1;
    if(per_seed>128) per_seed=128;
    for(int i=0;i<n;i++){
        uint64_t s=(uint64_t)seeds[i];
        applySeed(&c->g,DIM_OVERWORLD,s);
        StrongholdIter sh; initFirstStronghold(&sh,mc,s);
        for(int si=0;si<per_seed;si++){
            int rem=nextStronghold(&sh,&c->g);
            if(k<max_out){ out_seed[k]=s; out_x[k]=sh.pos.x; out_z[k]=sh.pos.z; }
            k++;
            if(rem<=0) break;
        }
    }
    return k;
}
static void xf(int lx,int ly,int lz,int mirFB,int rot,int px,int pz,int ox,int oy,int oz,int*wx,int*wy,int*wz){
    int n=lx,n3=lz; if(mirFB)n=-n; int tx,tz;
    switch(rot){case 2:tx=px+px-n;tz=pz+pz-n3;break; case 1:tx=px+pz-n3;tz=pz-px+n;break;
                case 3:tx=px-pz+n3;tz=px+pz-n;break; default:tx=n;tz=n3;break;}
    *wx=tx+ox; *wy=ly+oy; *wz=tz+oz;
}
// centre du portail en ruine d'une région ; retourne 0 si invalide
EXPORT int cb_ruined_portal(void*p,int mc,uint64_t seed,int regX,int regZ,
                            int*cx,int*cy,int*cz,int*underground,int*biome){
    Ctx*c=p; Pos pos;
    if(!getStructurePos(Ruined_Portal,mc,seed,regX,regZ,&pos)) return 0;
    int chx=pos.x>>4, chz=pos.z>>4;
    int bid=getBiomeAt(&c->g,1,pos.x,63,pos.z);
    uint64_t rng=chunkGenerateRnd(seed,chx,chz);
    int cat=getCategory(mc,bid), vbiome=-1, under=0,inmtn=0;
    if(cat==desert||cat==jungle||cat==swamp||cat==ocean||cat==nether_wastes) vbiome=cat;
    if(vbiome==-1){ if(bid==mangrove_swamp)vbiome=swamp;
      else if(bid==mountains||bid==mountain_edge||bid==wooded_mountains||bid==gravelly_mountains||bid==modified_gravelly_mountains||bid==savanna_plateau||bid==shattered_savanna||bid==shattered_savanna_plateau||bid==badlands||bid==eroded_badlands||bid==wooded_badlands_plateau||bid==modified_badlands_plateau||bid==modified_wooded_badlands_plateau||bid==snowy_taiga_mountains||bid==taiga_mountains||bid==stony_shore||bid==meadow||bid==frozen_peaks||bid==jagged_peaks||bid==stony_peaks||bid==snowy_slopes) vbiome=mountains; }
    if(vbiome==-1) vbiome=plains;
    if(vbiome==plains){ under=nextFloat(&rng)<0.5f; if(!under)nextFloat(&rng); }
    else if(vbiome==mountains){ inmtn=nextFloat(&rng)<0.5f; if(!inmtn)nextFloat(&rng); }
    else if(vbiome==jungle){ nextFloat(&rng); }
    int giant=nextFloat(&rng)<0.05f;
    int start=giant?1+nextInt(&rng,3):1+nextInt(&rng,10);
    int rot=nextInt(&rng,4);
    int cmir=nextFloat(&rng)<0.5f; int mirFB=!cmir;   // MC mirror = FRONT_BACK si cubiomes mir==0
    int ti=giant?10+start:start, sx=PSX[ti],sy=PSY[ti],sz=PSZ[ti], pvx=sx/2,pvz=sz/2;
    // centre footprint -> surface -> Y (findSuitableY simplifié, sans le scan)
    int fwx,fwy,fwz; xf(sx/2,0,sz/2,mirFB,rot,pvx,pvz,pos.x,0,pos.z,&fwx,&fwy,&fwz);
    float hy; mapApproxHeight(&hy,NULL,&c->g,&c->sn,fwx>>2,fwz>>2,1,1);
    int n=(int)hy-1, y, hi=n-sy;
    if(inmtn){ y=(70<hi)?(nextInt(&rng,hi-70+1)+70):hi; }
    else if(under){ y=(15<hi)?(nextInt(&rng,hi-15+1)+15):hi; }
    else if(vbiome==desert){ y=n-sy+(nextInt(&rng,7)+2); }
    else { y=n; }  // surface/ocean approx
    // box -> centre
    int X0=1<<30,X1=-(1<<30),Z0=1<<30,Z1=-(1<<30),q,r2,s2;
    for(int a=0;a<=1;a++)for(int b=0;b<=1;b++){
        xf(a?sx-1:0,0,b?sz-1:0,mirFB,rot,pvx,pvz,pos.x,y,pos.z,&q,&r2,&s2);
        if(q<X0)X0=q; if(q>X1)X1=q; if(s2<Z0)Z0=s2; if(s2>Z1)Z1=s2;
    }
    *cx=(X0+X1)/2; *cy=y+sy/2; *cz=(Z0+Z1)/2;
    *underground=under; *biome=vbiome;
    return 1;
}
EXPORT int cb_region_size(int mc){ StructureConfig sc; getStructureConfig(Ruined_Portal,mc,&sc); return sc.regionSize; }

// PRE-FILTRE 48-bit PUR : anneau du 1er stronghold + ruined portal le plus proche
// en X,Z. Aucun generator, aucun biome, aucune BFS. Retourne la distance Chebyshev min.
EXPORT int cb_prefilter(int mc,uint64_t seed,int*regX,int*regZ,int*dx,int*dz){
    StrongholdIter sh;
    Pos r=initFirstStronghold(&sh,mc,seed);   // anneau (low48, sans biome)
    int RS=40*16;
    int rgx=(int)floorf((float)r.x/RS), rgz=(int)floorf((float)r.z/RS);
    int best=1<<30,bx=0,bz=0,brx=0,brz=0;
    for(int a=-1;a<=1;a++)for(int b=-1;b<=1;b++){
        Pos p; if(!getStructurePos(Ruined_Portal,mc,seed,rgx+a,rgz+b,&p))continue;
        int ax=abs(p.x-r.x), az=abs(p.z-r.z), d=ax>az?ax:az;
        if(d<best){best=d;bx=p.x-r.x;bz=p.z-r.z;brx=rgx+a;brz=rgz+b;}
    }
    *regX=brx;*regZ=brz;*dx=bx;*dz=bz; return best;
}

// détail du ruined portal pour le calcul de chevauchement bloc-par-bloc.
// out[10] = {valid, template(1..13), rot, mirFB, ybottom, origX, origZ, cx, cy, cz}
EXPORT int cb_ruined_detail(void*p,int mc,uint64_t seed,int regX,int regZ,int*out){
    Ctx*c=p; Pos pos; out[0]=0;
    if(!getStructurePos(Ruined_Portal,mc,seed,regX,regZ,&pos)) return 0;
    int chx=pos.x>>4, chz=pos.z>>4;
    int bid=getBiomeAt(&c->g,1,pos.x,63,pos.z);
    uint64_t rng=chunkGenerateRnd(seed,chx,chz);
    int cat=getCategory(mc,bid), vbiome=-1, under=0,inmtn=0;
    if(cat==desert||cat==jungle||cat==swamp||cat==ocean||cat==nether_wastes) vbiome=cat;
    if(vbiome==-1){ if(bid==mangrove_swamp)vbiome=swamp;
      else if(bid==mountains||bid==mountain_edge||bid==wooded_mountains||bid==gravelly_mountains||bid==modified_gravelly_mountains||bid==savanna_plateau||bid==shattered_savanna||bid==shattered_savanna_plateau||bid==badlands||bid==eroded_badlands||bid==wooded_badlands_plateau||bid==modified_badlands_plateau||bid==modified_wooded_badlands_plateau||bid==snowy_taiga_mountains||bid==taiga_mountains||bid==stony_shore||bid==meadow||bid==frozen_peaks||bid==jagged_peaks||bid==stony_peaks||bid==snowy_slopes) vbiome=mountains; }
    if(vbiome==-1) vbiome=plains;
    if(vbiome==plains){ under=nextFloat(&rng)<0.5f; if(!under)nextFloat(&rng); }
    else if(vbiome==mountains){ inmtn=nextFloat(&rng)<0.5f; if(!inmtn)nextFloat(&rng); }
    else if(vbiome==jungle){ nextFloat(&rng); }
    int giant=nextFloat(&rng)<0.05f;
    int start=giant?1+nextInt(&rng,3):1+nextInt(&rng,10);
    int rot=nextInt(&rng,4);
    int cmir=nextFloat(&rng)<0.5f; int mirFB=!cmir;
    int ti=giant?10+start:start, sx=PSX[ti],sz=PSZ[ti], pvx=sx/2,pvz=sz/2;
    (void)under; (void)inmtn;
    // NOTE: the ruined portal Y (mapApproxHeight + surface noise) is NOT computed
    // here anymore — it cost ~27us/seed and the search only needs the X,Z
    // footprint. The exact Y is recomputed by the perfect checker. out[4]/out[8]=0.
    out[0]=1; out[1]=ti; out[2]=rot; out[3]=mirFB; out[4]=0; out[5]=pos.x; out[6]=pos.z;
    int X0=1<<30,X1=-(1<<30),Z0=1<<30,Z1=-(1<<30),q,r2,s2;
    for(int a=0;a<=1;a++)for(int b=0;b<=1;b++){ xf(a?sx-1:0,0,b?sz-1:0,mirFB,rot,pvx,pvz,pos.x,0,pos.z,&q,&r2,&s2);
        if(q<X0)X0=q;if(q>X1)X1=q;if(s2<Z0)Z0=s2;if(s2>Z1)Z1=s2; }
    out[7]=(X0+X1)/2; out[8]=0; out[9]=(Z0+Z1)/2;
    return 1;
}
// NATIVE batch scan : prefilter (ring) + snapped stronghold + ruined ORIGIN
// proximity gate (no ruined biome, no surface noise). Writes the passing seeds
// into out[] (capped at max_out) and returns the count. Python then runs the
// full validation (biome variant + BFS frames + footprint + chunk) only on these.
EXPORT int cb_corrupt_scan(void*p,int mc,uint64_t start,int n,int thr,int gate,
                           uint64_t*out,int max_out){
    Ctx*c=p;
    cb_gen_setup(c,mc);
    int RS=640, k=0;
    for(int i=0;i<n;i++){
        uint64_t s=start+(uint64_t)i;
        StrongholdIter sh; Pos r=initFirstStronghold(&sh,mc,s);
        int rgx=(int)floorf((float)r.x/RS), rgz=(int)floorf((float)r.z/RS), best=1<<30;
        for(int a=-1;a<=1;a++)for(int b=-1;b<=1;b++){
            Pos pp; if(!getStructurePos(Ruined_Portal,mc,s,rgx+a,rgz+b,&pp))continue;
            int ax=abs(pp.x-r.x),az=abs(pp.z-r.z),d=ax>az?ax:az; if(d<best)best=d;
        }
        if(best>=thr) continue;
        applySeed(&c->g,DIM_OVERWORLD,s);
        nextStronghold(&sh,&c->g);
        int sx=sh.pos.x, sz=sh.pos.z;
        int sgx=(int)floorf((float)sx/RS), sgz=(int)floorf((float)sz/RS), hit=0;
        for(int a=-1;a<=1&&!hit;a++)for(int b=-1;b<=1&&!hit;b++){
            Pos pp; if(!getStructurePos(Ruined_Portal,mc,s,sgx+a,sgz+b,&pp))continue;
            int ax=abs(pp.x-sx),az=abs(pp.z-sz),d=ax>az?ax:az; if(d<=gate)hit=1;
        }
        if(hit){ if(k<max_out)out[k]=s; k++; }
    }
    return k;
}
// 128-strongholds corrupted scan: for EVERY stronghold of each seed, apply the
// same cheap ruined-proximity gate as cb_corrupt_scan (position only, no biome /
// variant / surface noise). Writes (seed, sx, sz) for each surviving stronghold;
// Python then runs the full _validate_at on each. No 1st-stronghold prefilter
// here (any stronghold may have a nearby ruined). out_seed/out_x/out_z parallel
// arrays of length max_out; returns the number of surviving strongholds.
EXPORT int cb_corrupt_scan128(void*p,int mc,uint64_t start,int n,int gate,
                              uint64_t*out_seed,int*out_x,int*out_z,int max_out){
    Ctx*c=p;
    cb_gen_setup(c,mc);
    int RS=640, k=0;
    for(int i=0;i<n;i++){
        uint64_t s=start+(uint64_t)i;
        applySeed(&c->g,DIM_OVERWORLD,s);
        StrongholdIter sh; initFirstStronghold(&sh,mc,s);
        for(int si=0;si<128;si++){
            int rem=nextStronghold(&sh,&c->g);
            int sx=sh.pos.x, sz=sh.pos.z;
            int sgx=(int)floorf((float)sx/RS), sgz=(int)floorf((float)sz/RS), hit=0;
            for(int a=-1;a<=1&&!hit;a++)for(int b=-1;b<=1&&!hit;b++){
                Pos pp; if(!getStructurePos(Ruined_Portal,mc,s,sgx+a,sgz+b,&pp))continue;
                int ax=abs(pp.x-sx),az=abs(pp.z-sz),d=ax>az?ax:az; if(d<=gate)hit=1;
            }
            if(hit){ if(k<max_out){out_seed[k]=s;out_x[k]=sx;out_z[k]=sz;} k++; }
            if(rem<=0) break;
        }
    }
    return k;
}
// cb_corrupt_scan128 limited to the FIRST per_seed strongholds of each seed
// (3 = the first ring). Same gate, same outputs. 03/10.
EXPORT int cb_corrupt_scan_n(void*p,int mc,uint64_t start,int n,int gate,int per_seed,
                             uint64_t*out_seed,int*out_x,int*out_z,int max_out){
    Ctx*c=p;
    cb_gen_setup(c,mc);
    int RS=640, k=0;
    if(per_seed<1) per_seed=1;
    if(per_seed>128) per_seed=128;
    for(int i=0;i<n;i++){
        uint64_t s=start+(uint64_t)i;
        applySeed(&c->g,DIM_OVERWORLD,s);
        StrongholdIter sh; initFirstStronghold(&sh,mc,s);
        for(int si=0;si<per_seed;si++){
            int rem=nextStronghold(&sh,&c->g);
            int sx=sh.pos.x, sz=sh.pos.z;
            int sgx=(int)floorf((float)sx/RS), sgz=(int)floorf((float)sz/RS), hit=0;
            for(int a=-1;a<=1&&!hit;a++)for(int b=-1;b<=1&&!hit;b++){
                Pos pp; if(!getStructurePos(Ruined_Portal,mc,s,sgx+a,sgz+b,&pp))continue;
                int ax=abs(pp.x-sx),az=abs(pp.z-sz),d=ax>az?ax:az; if(d<=gate)hit=1;
            }
            if(hit){ if(k<max_out){out_seed[k]=s;out_x[k]=sx;out_z[k]=sz;} k++; }
            if(rem<=0) break;
        }
    }
    return k;
}
// Exact-ish ruined-portal bottom Y for ONE region (variant + surface height).
// Uses the surface noise (lazily inited, cached per seed). Returns -999 if no
// portal. Called only on the rare footprint+chunk candidates -> cheap overall.
EXPORT int cb_ruined_y(void*p,int mc,uint64_t seed,int regX,int regZ){
    Ctx*c=p; Pos pos;
    if(!getStructurePos(Ruined_Portal,mc,seed,regX,regZ,&pos)) return -999;
    int chx=pos.x>>4, chz=pos.z>>4;
    int bid=getBiomeAt(&c->g,1,pos.x,63,pos.z);
    uint64_t rng=chunkGenerateRnd(seed,chx,chz);
    int cat=getCategory(mc,bid), vbiome=-1, under=0,inmtn=0;
    if(cat==desert||cat==jungle||cat==swamp||cat==ocean||cat==nether_wastes) vbiome=cat;
    if(vbiome==-1){ if(bid==mangrove_swamp)vbiome=swamp;
      else if(bid==mountains||bid==mountain_edge||bid==wooded_mountains||bid==gravelly_mountains||bid==modified_gravelly_mountains||bid==savanna_plateau||bid==shattered_savanna||bid==shattered_savanna_plateau||bid==badlands||bid==eroded_badlands||bid==wooded_badlands_plateau||bid==modified_badlands_plateau||bid==modified_wooded_badlands_plateau||bid==snowy_taiga_mountains||bid==taiga_mountains||bid==stony_shore||bid==meadow||bid==frozen_peaks||bid==jagged_peaks||bid==stony_peaks||bid==snowy_slopes) vbiome=mountains; }
    if(vbiome==-1) vbiome=plains;
    if(vbiome==plains){ under=nextFloat(&rng)<0.5f; if(!under)nextFloat(&rng); }
    else if(vbiome==mountains){ inmtn=nextFloat(&rng)<0.5f; if(!inmtn)nextFloat(&rng); }
    else if(vbiome==jungle){ nextFloat(&rng); }
    int giant=nextFloat(&rng)<0.05f;
    int start=giant?1+nextInt(&rng,3):1+nextInt(&rng,10);
    int rot=nextInt(&rng,4);
    int cmir=nextFloat(&rng)<0.5f; int mirFB=!cmir;
    int ti=giant?10+start:start, sx=PSX[ti],sy=PSY[ti],sz=PSZ[ti], pvx=sx/2,pvz=sz/2;
    if(!c->surf_ok || c->surf_seed!=seed){ initSurfaceNoise(&c->sn,DIM_OVERWORLD,seed); c->surf_seed=seed; c->surf_ok=1; }
    int fwx,fwy,fwz; xf(sx/2,0,sz/2,mirFB,rot,pvx,pvz,pos.x,0,pos.z,&fwx,&fwy,&fwz);
    float hy; mapApproxHeight(&hy,NULL,&c->g,&c->sn,fwx>>2,fwz>>2,1,1);
    int n=(int)hy-1, y, hi=n-sy;
    if(inmtn){ y=(70<hi)?(nextInt(&rng,hi-70+1)+70):hi; }
    else if(under){ y=(15<hi)?(nextInt(&rng,hi-15+1)+15):hi; }
    else if(vbiome==desert){ y=n-sy+(nextInt(&rng,7)+2); }
    else { y=n; }
    (void)sz;
    return y;
}
// 1.18+ ruined-portal variant via setLargeFeatureSeed (Java LCG / LegacyRandom),
// the new structure-start system. out[7] = {valid, chunkX, chunkZ, ti, rot,
// mirFB, setup} ; setup 0=underground, 1=on_land_surface. Standard overworld
// ruined_portal.json has 2 setups (underground w0.5 airpocket1.0 ;
// on_land_surface w0.5 airpocket0.5). Footprint X,Z is EXACT (validated vs the
// 1.18.1 checker); the Y is left to the checker. RNG matches Java bit-for-bit.
EXPORT int cb_ruined_118(void*p,int mc,uint64_t seed,int regX,int regZ,int*out){
    (void)p;
    Pos pos;
    if(!getStructurePos(Ruined_Portal,mc,seed,regX,regZ,&pos)){ out[0]=0; return 0; }
    int cx=pos.x>>4, cz=pos.z>>4;
    // setLargeFeatureSeed(seed, cx, cz)
    uint64_t rng; setSeed(&rng,seed);
    int64_t a=(int64_t)nextLong(&rng);
    int64_t b=(int64_t)nextLong(&rng);
    setSeed(&rng,(uint64_t)((int64_t)cx*a ^ (int64_t)cz*b ^ (int64_t)seed));
    // setup selection (2 setups, weights 0.5/0.5 ; underground first in the json)
    int setup = (nextFloat(&rng) < 0.5f) ? 0 : 1;
    if(setup==1) nextFloat(&rng);          // on_land_surface air-pocket (prob 0.5)
    int giant = nextFloat(&rng) < 0.05f;
    int idx = nextInt(&rng, giant?3:10);
    int ti = giant ? 11+idx : 1+idx;        // SSA template index (1..13)
    int rot = nextInt(&rng,4);
    int mirFB = nextFloat(&rng) >= 0.5f;    // FRONT_BACK iff nextFloat >= 0.5
    out[0]=1; out[1]=cx; out[2]=cz; out[3]=ti; out[4]=rot; out[5]=mirFB; out[6]=setup;
    return 1;
}
