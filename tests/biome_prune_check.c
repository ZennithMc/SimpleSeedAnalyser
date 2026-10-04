/* biome_prune_check.c — exactness of the p92 fast biome paths.
 *
 * Compares, on random seeds / positions / biome sets / scales, for every
 * 1.18+ version, normal and Large Biomes:
 *   ssa_biome_at()        vs cubiomes getBiomeAt()
 *   fast_check_biomes()   vs cubiomes checkForBiomes()   (biome_filter)
 *   monte_eval fast path  vs getBiomeAt path              (biome_sample)
 * and sampleBiomeNoisePruned() against sampleBiomeNoise() cell by cell.
 * Every line must end with « bad 0 ».
 *
 * Build (from the SSA folder, Linux / MinGW):
 *   gcc -O2 -fopenmp -fwrapv -Icubiomes_src -o prune_check tests/biome_prune_check.c \
 *       cubiomes_src/{noise,biomes,layers,biomenoise,generator,finders,util,quadbase,\
 *       noise_bridge,height_bridge,biome_filter_bridge,gaider_fast,drawing_bridge,\
 *       draw_engine,corrupted_bridge,biome_gpu_bridge,noise_gpu_bridge}.c -lm -lpthread
 *   ./prune_check [jobs]        (default 24; 96 = the p92 validation, ~25 min on 2 cores)
 */
#include "../cubiomes_src/session_engine.c"
#include <omp.h>
static int ow_ids[64][256], ow_n[64];
int main(int argc, char **argv){
  int mcs[]={MC_1_18,MC_1_19_2,MC_1_19,MC_1_20,MC_1_21_1,MC_1_21_3,MC_1_21_WD,MC_1_21_5,MC_1_21,MC_26_1,MC_26_2,MC_26_3};
  int NJ = argc>1?atoi(argv[1]):24;
  for(int mi=0;mi<12;mi++){int mc=mcs[mi]; for(int i=0;i<192;i++) if(isOverworld(mc,i) && (i<64||i>=128)) ow_ids[mc][ow_n[mc]++]=i;}
  long nbf=0,badbf=0,nat=0,badat=0,nmc=0,badmc=0, pos=0, npr=0, badpr=0;
  #pragma omp parallel for schedule(dynamic) reduction(+:nbf,badbf,nat,badat,nmc,badmc,pos,npr,badpr)
  for(int job=0;job<NJ;job++){
    int mc=mcs[job%12]; uint64_t rs=0xD1B54A32D192ED03ULL*(job+7);
    #define R() (rs^=rs<<13,rs^=rs>>7,rs^=rs<<17,rs)
    SsaEnv *env=(SsaEnv*)calloc(1,sizeof(SsaEnv));
    int large=(job/12)&1;
    setupGenerator(&env->g,mc,large?LARGE_BIOMES:0);
    env->mc=mc; env->large=large;
    Generator *gref=(Generator*)malloc(sizeof(Generator)); setupGenerator(gref,mc,large?LARGE_BIOMES:0);
    for(int sd=0;sd<6;sd++){
      env->seed=R(); applySeed(&env->g,0,env->seed); applySeed(gref,0,env->seed);
      /* getBiomeAt */
      for(int k=0;k<3000;k++){ int sc[]={1,4,16,64,256}; int scale=sc[R()%5]; int x=(int)(R()%20000)-10000, z=(int)(R()%20000)-10000; int y=(int)(R()%80)-16;
        int a=getBiomeAt(gref,scale,x,y,z), b=ssa_biome_at(&env->g,NULL,0,scale,x,y,z,0); nat++; if(a!=b){badat++; if(badat<5)printf("AT mc=%d sc=%d %d/%d\n",mc,scale,a,b);}
        /* pruned: -2 only when a is none of the candidates, else a */
        int cid[3], nc=1+R()%3; for(int q=0;q<nc;q++) cid[q]=(R()%4==0)?a:ow_ids[mc][R()%ow_n[mc]];
        BiomePrune bp; if(a>=0 && setupBiomePrune(&bp,mc,cid,nc)){ int p=ssa_biome_at(&env->g,&bp,(1u<<nc)-1,scale,x,y,z,(k&1)?SAMPLE_SSA_SCATTERED:0); int in=0; for(int q=0;q<nc;q++) in|=cid[q]==a;
          npr++; if((p==-2 && in) || (p!=-2 && p!=a)){badpr++; printf("PRUNE mc=%d sc=%d truth=%d got=%d\n",mc,scale,a,p);} } }
      /* biome filter */
      for(int k=0;k<25;k++){
        CondCache cache; memset(&cache,0,sizeof cache);
        int inc[4],exc[3],ni=1+R()%3,ne=(R()%3==0)?1+R()%2:0, any=R()%3==0;
        for(int q=0;q<ni;q++) inc[q]=ow_ids[mc][R()%ow_n[mc]];
        for(int q=0;q<ne;q++) exc[q]=ow_ids[mc][R()%ow_n[mc]];
        if(any) setupBiomeFilter(&cache.bf,mc,BF_FORCED_OCEAN,NULL,0,exc,ne,inc,ni);
        else setupBiomeFilter(&cache.bf,mc,BF_FORCED_OCEAN,inc,ni,exc,ne,NULL,0);
        int sc[]={1,4,4,4,16,64,256}; int scale=sc[R()%7];
        int half = scale==1? 8+R()%64 : (scale==4? 16+R()%600 : 64+R()%3000);
        int cx=(int)(R()%4000)-2000, cz=(int)(R()%4000)-2000;
        int s_shift = scale==1?0:scale==4?2:scale==16?4:scale==64?6:8;
        int x1=cx-half,z1=cz-half,x2=cx+half,z2=cz+half;
        Range r; r.scale=scale; r.x=x1>>s_shift; r.z=z1>>s_shift; r.sx=(x2>>s_shift)-r.x+1; r.sz=(z2>>s_shift)-r.z+1;
        int by=(int)(R()%320)-64; r.y = s_shift>0 ? (by>>2) : by; r.sy=1;
        int ref=checkForBiomes(gref,NULL,r,0,env->seed,&cache.bf,NULL)>0;
        int f=fast_check_biomes(env,&cache,r,0);
        nbf++; pos+=ref; if(f!=ref){badbf++; printf("BF mc=%d sc=%d any=%d ni=%d ne=%d ref=%d fast=%d\n",mc,scale,any,ni,ne,ref,f);}
      }
      /* monte */
      for(int k=0;k<6;k++){
        int inc[2],exc[2],ni=1+R()%2,ne=R()%2; for(int q=0;q<ni;q++) inc[q]=ow_ids[mc][R()%ow_n[mc]]; for(int q=0;q<ne;q++) exc[q]=ow_ids[mc][R()%ow_n[mc]];
        int sc[]={1,4,4,16}; int scale=sc[R()%4]; int half=scale==1?64:512; int s_shift=scale==1?0:scale==4?2:4;
        Range r; r.scale=scale; r.x=(-half)>>s_shift; r.z=(-half)>>s_shift; r.sx=(half>>s_shift)-r.x+1; r.sz=r.sx; r.y= s_shift?16:64; r.sy=1;
        double cov=0.01+(R()%50)/100.0;
        MonteData d1; memset(&d1,0,sizeof d1); d1.incl=inc;d1.n_incl=ni;d1.excl=exc;d1.n_excl=ne; d1.rmax_sq=(R()&1)? (int64_t)half*half:0;
        MonteData d2=d1;
        CondCache cache; memset(&cache,0,sizeof cache);
        uint64_t bb=0,mm=0; for(int q=0;q<ni+ne;q++){int id=q<ni?inc[q]:exc[q-ni]; if(id<128)bb|=1ULL<<id; else mm|=1ULL<<(id-128);}
        cache_setup_prune(&cache,mc,bb,mm);
        d2.fast=1; if(cache.bp_state>0){d2.bp=&cache.bp; d2.alive=(1u<<cache.bp.n)-1;}
        uint64_t r1,r2; setSeed(&r1,env->seed); setSeed(&r2,env->seed);
        int o1=monteCarloBiomes(gref,r,&r1,cov,0.95,monte_eval,&d1);
        int o2=monteCarloBiomes(&env->g,r,&r2,cov,0.95,monte_eval,&d2);
        nmc++; if(o1!=o2||d1.n!=d2.n||d1.xsum!=d2.xsum||d1.zsum!=d2.zsum){badmc++; printf("MC mc=%d %d/%d n %lld/%lld\n",mc,o1,o2,(long long)d1.n,(long long)d2.n);}
      }
    }
    free(env); free(gref);
  }
  printf("getBiomeAt   %ld bad %ld\npruned cells %ld bad %ld\nbiome_filter %ld (%ld positive) bad %ld\nbiome_sample %ld bad %ld\n",nat,badat,npr,badpr,nbf,pos,badbf,nmc,badmc);
  return (badat||badpr||badbf||badmc) ? 1 : 0;
}
