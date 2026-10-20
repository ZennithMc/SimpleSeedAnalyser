"""Kernel-math emulation (structure_cuda.cu d_test_job, algo 0/3) for the
new Nether/End GPU jobs: every seed the C engine accepts must pass."""
import sys, random, copy
import os; _H=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0,os.path.dirname(_H)); sys.path.insert(0,_H)
import structure_cuda as sc, custom_search as cs, diff_engine as d
from cubiomes_wrap import CubiomesLib
K=0x5deece66d; M=(1<<48)-1; U=(1<<64)-1
def feat(salt,cr,seed,rx,rz):
    s=(seed+rx*341873128712+rz*132897987541+salt)&U
    s=(s^K)&U; s=(s*K+0xb)&M
    if cr&(cr-1): px=(s>>17)%cr; s=(s*K+0xb)&M; pz=(s>>17)%cr
    else: px=(cr*(s>>17))>>31; s=(s*K+0xb)&M; pz=(cr*(s>>17))>>31
    return px,pz
def large(salt,cr,seed,rx,rz):
    s=(seed+rx*341873128712+rz*132897987541+salt)&U; s=(s^K)&U
    v=[]
    for _ in range(4): s=(s*K+0xb)&M; v.append((s>>17)%cr)
    return (v[0]+v[1])>>1,(v[2]+v[3])>>1
def s32(v): v&=0xffffffff; return v-(1<<32) if v>>31 else v
def jpass(j,seed):
    hits=0
    for rz in range(j.rz1,j.rz2+1):
        for rx in range(j.rx1,j.rx2+1):
            if j.algo_kind==0: a,b=feat(j.salt,j.chunk_range,seed,rx,rz)
            else: a,b=large(j.salt,j.chunk_range,seed,rx,rz)
            x=s32((rx*j.region_size+a)<<4); z=s32((rz*j.region_size+b)<<4)
            if j.algo_kind==3 and x*x+z*z<1008*1008: continue
            ins=((x-j.cx)**2+(z-j.cz)**2<j.rmax_sq) if j.rmax_sq else (j.x1<=x<=j.x2 and j.z1<=z<=j.z2)
            if ins:
                hits+=1
                if hits>=j.min_count: return True
    return False
S=d.S
lib=CubiomesLib()
CASES={"fortress r400":[S("fortress",r=400)],"bastion r300":[S("bastion",r=300)],
 "2 fortresses r900":[S("fortress",r=900,inst=2)],"portalN r150":[S("ruined_portal_nether",r=150)],
 "end city r1600":[S("end_city",r=1600)],"fortress box":[S("fortress",box=(-300,100,500,700))],
 "fort + NOT bastion":[S("fortress",r=500),S("logic_not",rel=1),S("bastion",rel=2,r=300)]}
rng=random.Random(3); bad=0
for mc in (19,21,34):
  for name,conds in CASES.items():
    for c in conds: c["params"]["use_gpu"]=True
    act=cs.active_conditions(copy.deepcopy(conds))
    jobs,sup=sc.build_jobs(act,lib,mc)
    npass=nacc=0
    for _ in range(300):
        sd=rng.getrandbits(64)
        acc=cs.check_seed(conds, sd-(1<<63) if sd>>63 else sd, mc)[0]
        gp=all(jpass(j,sd) for j in jobs)
        npass+=gp; nacc+=acc
        if acc and not gp: bad+=1; print("MISS",mc,name,sd)
    print(mc,name,"jobs",len(jobs),"engine",nacc,"gpu-pass",npass)
print("bad",bad)
