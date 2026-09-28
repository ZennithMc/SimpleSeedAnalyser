/*
 * structure_cuda.cu — Filtre 48-bit GPU des positions de structures pour SSA.
 *
 * Reproduit bit-perfect le pré-filtre CPU ssa_test_seed_pos_only : le GPU calcule
 * UNIQUEMENT les positions de structures (RNG 48-bit, massivement parallèle) et
 * renvoie les seeds dont toutes les structures tombent dans la zone. Le CPU
 * vérifie ensuite biomes + autres conditions (sessions mixtes OK). C'est
 * l'équivalent GPU de la passe 48-bit de cubiomes.
 *
 * getStructureConfig n'est PAS réimplémenté ici : le CPU passe salt/regionSize/
 * chunkRange via GpuStructJob. Le GPU ne fait que l'arithmétique.
 */
#include <stdint.h>
#include <cuda_runtime.h>
#define SSA_UNIT "structure_cuda"
#include "ssa_cuda_common.cuh"

#ifdef _WIN32
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT __attribute__((visibility("default")))
#endif

#define ALGO_FEATURE  0
#define ALGO_LARGE    1
#define ALGO_OUTPOST  2
#define ALGO_END_CITY 3

struct GpuStructJob {
    int32_t algo_kind, salt, region_size, chunk_range;
    int32_t rx1, rz1, rx2, rz2;
    int32_t x1, z1, x2, z2;
    int64_t rmax_sq;
    int32_t cx, cz, min_count, exclude, _pad;
};

#define LCG_K 0x5deece66dULL
#define LCG_B 0xbULL
#define LCG_M ((1ULL << 48) - 1)

__device__ __forceinline__ void d_setSeed(uint64_t *s, uint64_t v){ *s=(v^LCG_K)&LCG_M; }
__device__ __forceinline__ int d_next(uint64_t *s,int bits){ *s=(*s*LCG_K+LCG_B)&LCG_M; return (int)((int64_t)*s>>(48-bits)); }
__device__ __forceinline__ int d_nextInt(uint64_t *s,int n){
    int m=n-1;
    if((m&n)==0){ uint64_t x=(uint64_t)n*(uint64_t)d_next(s,31); return (int)((int64_t)x>>31); }
    int bits,val; do{ bits=d_next(s,31); val=bits%n; }while((int32_t)((uint32_t)bits-val+m)<0); return val;
}
__device__ __forceinline__ void d_featureChunk(int32_t salt,int32_t cr,uint64_t seed,int rx,int rz,int*ox,int*oz){
    seed=seed+(uint64_t)rx*341873128712ULL+(uint64_t)rz*132897987541ULL+(uint64_t)salt;
    seed=(seed^LCG_K); seed=(seed*LCG_K+LCG_B)&LCG_M;
    uint64_t r=(uint64_t)cr; int px,pz;
    if(r&(r-1)){ px=(int)((seed>>17)%r); seed=(seed*LCG_K+LCG_B)&LCG_M; pz=(int)((seed>>17)%r); }
    else{ px=(int)((r*(seed>>17))>>31); seed=(seed*LCG_K+LCG_B)&LCG_M; pz=(int)((r*(seed>>17))>>31); }
    *ox=px; *oz=pz;
}
__device__ __forceinline__ void d_largeChunk(int32_t salt,int32_t cr,uint64_t seed,int rx,int rz,int*ox,int*oz){
    seed=seed+(uint64_t)rx*341873128712ULL+(uint64_t)rz*132897987541ULL+(uint64_t)salt;
    seed=(seed^LCG_K); int px,pz;
    seed=(seed*LCG_K+LCG_B)&LCG_M; px=(int)((seed>>17)%cr);
    seed=(seed*LCG_K+LCG_B)&LCG_M; px+=(int)((seed>>17)%cr);
    seed=(seed*LCG_K+LCG_B)&LCG_M; pz=(int)((seed>>17)%cr);
    seed=(seed*LCG_K+LCG_B)&LCG_M; pz+=(int)((seed>>17)%cr);
    *ox=px>>1; *oz=pz>>1;
}
__device__ __forceinline__ void d_setAttemptSeed(uint64_t*s,int cx,int cz){
    *s^=(uint64_t)(cx>>4)^((uint64_t)(cz>>4)<<4); d_setSeed(s,*s); d_next(s,31);
}
__device__ __forceinline__ int d_structurePos(const GpuStructJob*j,uint64_t seed,int rx,int rz,int*px,int*pz){
    int cxr,czr;
    switch(j->algo_kind){
    case ALGO_FEATURE:
        d_featureChunk(j->salt,j->chunk_range,seed,rx,rz,&cxr,&czr);
        *px=(int)(((uint64_t)rx*j->region_size+cxr)<<4); *pz=(int)(((uint64_t)rz*j->region_size+czr)<<4); return 1;
    case ALGO_LARGE:
        d_largeChunk(j->salt,j->chunk_range,seed,rx,rz,&cxr,&czr);
        *px=(int)(((uint64_t)rx*j->region_size+cxr)<<4); *pz=(int)(((uint64_t)rz*j->region_size+czr)<<4); return 1;
    case ALGO_OUTPOST:{
        d_featureChunk(j->salt,j->chunk_range,seed,rx,rz,&cxr,&czr);
        int x=(int)(((uint64_t)rx*j->region_size+cxr)<<4),z=(int)(((uint64_t)rz*j->region_size+czr)<<4);
        *px=x; *pz=z; uint64_t s2=seed; d_setAttemptSeed(&s2,x>>4,z>>4); return d_nextInt(&s2,5)==0; }
    case ALGO_END_CITY:{
        d_largeChunk(j->salt,j->chunk_range,seed,rx,rz,&cxr,&czr);
        int x=(int)(((uint64_t)rx*j->region_size+cxr)<<4),z=(int)(((uint64_t)rz*j->region_size+czr)<<4);
        *px=x; *pz=z; return ((int64_t)x*x+(int64_t)z*z)>=1008LL*1008LL; }
    default: return 0;
    }
}
__device__ __forceinline__ int d_test_job(const GpuStructJob*j,uint64_t seed){
    int hits=0;
    for(int rz=j->rz1;rz<=j->rz2;rz++) for(int rx=j->rx1;rx<=j->rx2;rx++){
        int px,pz; if(!d_structurePos(j,seed,rx,rz,&px,&pz)) continue;
        int inside;
        if(j->rmax_sq){ int64_t dx=px-j->cx,dz=pz-j->cz; inside=(dx*dx+dz*dz<j->rmax_sq); }
        else inside=(px>=j->x1&&px<=j->x2&&pz>=j->z1&&pz<=j->z2);
        if(!inside) continue;
        hits++; if(hits>=j->min_count) return j->exclude?0:1;
    }
    return j->exclude?1:0;
}

/* Kernels avec grid-stride loop : une grille de taille fixe couvre un batch
 * arbitrairement grand, chaque thread traitant plusieurs graines espacées du
 * nombre total de threads. Évite de lancer des millions de blocs. */
__global__ void k_struct_filter(int64_t seed_start,int64_t n,const GpuStructJob*jobs,int n_jobs,
        int64_t*out_seeds,int*out_count,int out_cap){
    int64_t stride=(int64_t)gridDim.x*blockDim.x;
    for(int64_t idx=(int64_t)blockIdx.x*blockDim.x+threadIdx.x; idx<n; idx+=stride){
        uint64_t seed=(uint64_t)(seed_start+idx);
        int ok=1;
        for(int j=0;j<n_jobs;j++) if(!d_test_job(&jobs[j],seed)){ ok=0; break; }
        if(ok){ int slot=atomicAdd(out_count,1); if(slot<out_cap) out_seeds[slot]=(int64_t)seed; }
    }
}
__global__ void k_struct_filter_fam48(int64_t low_start,int64_t n,const GpuStructJob*jobs,int n_jobs,
        int64_t*out_seeds,int*out_count,int out_cap){
    int64_t stride=(int64_t)gridDim.x*blockDim.x;
    for(int64_t idx=(int64_t)blockIdx.x*blockDim.x+threadIdx.x; idx<n; idx+=stride){
        uint64_t low=(uint64_t)(low_start+idx)&LCG_M;
        int ok=1;
        for(int j=0;j<n_jobs;j++) if(!d_test_job(&jobs[j],low)){ ok=0; break; }
        if(ok){ int slot=atomicAdd(out_count,1); if(slot<out_cap) out_seeds[slot]=(int64_t)low; }
    }
}

extern "C" {
EXPORT int structure_cuda_available(){ static int c=-1; if(c<0) c=(ssa_cuda_selftest()==0)?1:0; return c; }
/* Buffers GPU PERSISTANTS : alloués une seule fois puis réutilisés à chaque
 * batch. L'ancienne version faisait cudaMalloc + cudaFree + cudaMemcpy(jobs) à
 * CHAQUE appel — ces opérations sont synchronisantes et coûteuses, elles
 * créaient des "bulles" où le GPU restait inactif entre deux batches (GPU à
 * 10-20%, en dents de scie). On garde les buffers et on ne recopie les jobs que
 * s'ils changent. */
static GpuStructJob *g_d_jobs   = nullptr;
static int64_t      *g_d_out    = nullptr;
static int          *g_d_count  = nullptr;
static int           g_jobs_cap = 0;     /* capacité allouée pour d_jobs */
static int64_t       g_out_cap  = 0;     /* capacité allouée pour d_out  */
static uint64_t      g_jobs_sig = 0;     /* signature des jobs en cache  */

static uint64_t _jobs_signature(const GpuStructJob*jobs,int n_jobs){
    /* Hash simple pour détecter si les jobs ont changé depuis le dernier appel. */
    uint64_t h=1469598103934665603ULL;
    const unsigned char*p=(const unsigned char*)jobs;
    size_t nbytes=(size_t)n_jobs*sizeof(GpuStructJob);
    for(size_t i=0;i<nbytes;i++){ h^=p[i]; h*=1099511628211ULL; }
    return h^((uint64_t)n_jobs<<1);
}

EXPORT int64_t structure_cuda_filter(int64_t seed_start,int64_t n,const GpuStructJob*jobs,int n_jobs,
        int64_t*out_seeds,int out_cap,int fam48){
    if(n<=0||n_jobs<=0) return 0;
    int64_t result=-1;

    /* (Ré)allouer d_jobs seulement si la capacité est insuffisante. */
    if(n_jobs>g_jobs_cap){
        if(g_d_jobs){ cudaFree(g_d_jobs); g_d_jobs=nullptr; }
        SSA_CK(cudaMalloc(&g_d_jobs,(size_t)n_jobs*sizeof(GpuStructJob)));
        g_jobs_cap=n_jobs; g_jobs_sig=0;  /* forcer la recopie */
    }
    /* (Ré)allouer d_out seulement si la capacité est insuffisante. */
    if((int64_t)out_cap>g_out_cap){
        if(g_d_out){ cudaFree(g_d_out); g_d_out=nullptr; }
        { cudaError_t _e=cudaMalloc(&g_d_out,(size_t)out_cap*sizeof(int64_t)); if(_e!=cudaSuccess){ g_d_out=nullptr; g_out_cap=0; ssa_set_error(_e,"cudaMalloc(d_out)",__LINE__); return -1; } }
        g_out_cap=out_cap;
    }
    /* Allouer le compteur une seule fois. */
    if(!g_d_count){
        SSA_CK(cudaMalloc(&g_d_count,sizeof(int)));
    }
    /* Recopier les jobs UNIQUEMENT s'ils ont changé (signature). */
    uint64_t sig=_jobs_signature(jobs,n_jobs);
    if(sig!=g_jobs_sig){
        SSA_CK(cudaMemcpy(g_d_jobs,jobs,(size_t)n_jobs*sizeof(GpuStructJob),cudaMemcpyHostToDevice));
        g_jobs_sig=sig;
    }
    SSA_CK(cudaMemset(g_d_count,0,sizeof(int)));
    {
        /* Lancements découpés (~SSA_TARGET_MS chacun) : un lot de 256 M lows sur un
         * filtre lourd durait ~0,9 s sur une RTX 5080, donc > 2 s (watchdog Windows,
         * reset pilote) sur une carte 3× plus lente. Le compteur et le buffer de
         * sortie sont partagés entre sous-lancements : sémantique inchangée. */
        static SsaChunker ck(1LL << 24, 1LL << 16, 1LL << 30);
        const int threads=256;
        for(int64_t off=0; off<n; ){
            int64_t cur = (n-off < ck.chunk) ? (n-off) : ck.chunk;
            int64_t b64=(cur+threads-1)/threads;
            if(b64>65536LL) b64=65536LL;   /* plafond grid-stride */
            if(b64<1LL) b64=1LL;
            int blocks=(int)b64;
            ck.begin();
            if(fam48) k_struct_filter_fam48<<<blocks,threads>>>(seed_start+off,cur,g_d_jobs,n_jobs,g_d_out,g_d_count,out_cap);
            else      k_struct_filter<<<blocks,threads>>>(seed_start+off,cur,g_d_jobs,n_jobs,g_d_out,g_d_count,out_cap);
            SSA_CK_LAUNCH();
            ck.end();
            off += cur;
        }
    }
    {
        int h=0; SSA_CK(cudaMemcpy(&h,g_d_count,sizeof(int),cudaMemcpyDeviceToHost));
        int tc=h<out_cap?h:out_cap;
        if(tc>0) SSA_CK(cudaMemcpy(out_seeds,g_d_out,(size_t)tc*sizeof(int64_t),cudaMemcpyDeviceToHost));
        result=(int64_t)h;
    }
    return result;
}
/* Libère les buffers GPU persistants (optionnel, à la fermeture). */
EXPORT void structure_cuda_free(){
    if(g_d_jobs){ cudaFree(g_d_jobs); g_d_jobs=nullptr; }
    if(g_d_out){ cudaFree(g_d_out); g_d_out=nullptr; }
    if(g_d_count){ cudaFree(g_d_count); g_d_count=nullptr; }
    g_jobs_cap=0; g_out_cap=0; g_jobs_sig=0;
}
}
