// noise_gpu_bridge.c — host-side extraction of the seed-INDEPENDENT data the
// 1.18+ noise GPU kernel needs: the flattened depth spline and the biome-tree
// tables for the version. Compiled INTO libcubiomes; noise_gpu.py calls
// noise_gpu_get_tables(mc,...) once per version, then hands the result to
// noise_gpu.dll's noise_gpu_biomescan.
#include "generator.h"
#include "biomenoise.h"
#include "noise_gpu_kernel.cuh"     // SplineFlatG
#include "tables/btree18.h"
#include "tables/btree192.h"
#include "tables/btree19.h"
#include "tables/btree20.h"
#include "tables/btree21wd.h"
#include "tables/btree262.h"
#include "tables/btree263.h"

#ifdef _WIN32
  #define EXPORT __declspec(dllexport)
#else
  #define EXPORT __attribute__((visibility("default")))
#endif

// flatten cubiomes' Spline pointer tree into an indexable array (DFS, immediate
// child assignment — A[me] is a stable slot, children get indices > me).
static int ng_flatten(const Spline *s, SplineFlatG *A, int *cnt){
    int me = (*cnt)++;
    if (s->len == 1){ A[me].len = 1; A[me].fixval = ((FixSpline*)s)->val; return me; }
    A[me].len = s->len; A[me].typ = s->typ;
    for (int i = 0; i < s->len; i++){ A[me].loc[i] = s->loc[i]; A[me].der[i] = s->der[i]; }
    for (int i = 0; i < s->len; i++) A[me].val[i] = ng_flatten(s->val[i], A, cnt);
    return me;
}

// returns 1 if this version is supported (1.18+ noise), 0 otherwise.
// spline_out must hold >= 400 SplineFlatG. The btree pointers reference static
// tables inside libcubiomes (valid for the process lifetime).
EXPORT int noise_gpu_get_tables(int mc,
    SplineFlatG *spline_out, int *spline_n,
    const uint32_t **steps, int *steps_n,
    const int32_t  **param, int *param_n,
    const uint64_t **nodes, int *nodes_n,
    unsigned int *order)
{
    if (mc < MC_1_18) return 0;

    Generator g;
    setupGenerator(&g, mc, 0);
    applySeed(&g, DIM_OVERWORLD, 1ULL);   // builds bn.sp (seed-independent shape)
    int cnt = 0;
    ng_flatten(g.bn.sp, spline_out, &cnt);
    *spline_n = cnt;

    if (mc >= MC_26_3){
        *steps = btree263_steps; *steps_n = sizeof(btree263_steps)/sizeof(uint32_t);
        *param = &btree263_param[0][0]; *param_n = sizeof(btree263_param)/sizeof(int32_t);
        *nodes = btree263_nodes; *nodes_n = sizeof(btree263_nodes)/sizeof(uint64_t);
        *order = btree263_order;
    } else if (mc >= MC_26_2){
        *steps = btree262_steps; *steps_n = sizeof(btree262_steps)/sizeof(uint32_t);
        *param = &btree262_param[0][0]; *param_n = sizeof(btree262_param)/sizeof(int32_t);
        *nodes = btree262_nodes; *nodes_n = sizeof(btree262_nodes)/sizeof(uint64_t);
        *order = btree262_order;
    } else if (mc >= MC_1_21_WD){
        *steps = btree21wd_steps; *steps_n = sizeof(btree21wd_steps)/sizeof(uint32_t);
        *param = &btree21wd_param[0][0]; *param_n = sizeof(btree21wd_param)/sizeof(int32_t);
        *nodes = btree21wd_nodes; *nodes_n = sizeof(btree21wd_nodes)/sizeof(uint64_t);
        *order = btree21wd_order;
    } else if (mc >= MC_1_20_6){
        *steps = btree20_steps; *steps_n = sizeof(btree20_steps)/sizeof(uint32_t);
        *param = &btree20_param[0][0]; *param_n = sizeof(btree20_param)/sizeof(int32_t);
        *nodes = btree20_nodes; *nodes_n = sizeof(btree20_nodes)/sizeof(uint64_t);
        *order = btree20_order;
    } else if (mc >= MC_1_19_4){
        *steps = btree19_steps; *steps_n = sizeof(btree19_steps)/sizeof(uint32_t);
        *param = &btree19_param[0][0]; *param_n = sizeof(btree19_param)/sizeof(int32_t);
        *nodes = btree19_nodes; *nodes_n = sizeof(btree19_nodes)/sizeof(uint64_t);
        *order = btree19_order;
    } else if (mc >= MC_1_19_2){
        *steps = btree192_steps; *steps_n = sizeof(btree192_steps)/sizeof(uint32_t);
        *param = &btree192_param[0][0]; *param_n = sizeof(btree192_param)/sizeof(int32_t);
        *nodes = btree192_nodes; *nodes_n = sizeof(btree192_nodes)/sizeof(uint64_t);
        *order = btree192_order;
    } else {
        *steps = btree18_steps; *steps_n = sizeof(btree18_steps)/sizeof(uint32_t);
        *param = &btree18_param[0][0]; *param_n = sizeof(btree18_param)/sizeof(int32_t);
        *nodes = btree18_nodes; *nodes_n = sizeof(btree18_nodes)/sizeof(uint64_t);
        *order = btree18_order;
    }
    return 1;
}
