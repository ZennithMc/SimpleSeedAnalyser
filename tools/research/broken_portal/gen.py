import sys, random, ctypes; sys.path.insert(0,'/home/claude/ssa')
import ruined_portal as rp, cubiomes_wrap, stronghold_eyes as se
lib=ctypes.CDLL(cubiomes_wrap._find_lib()); rp._bind(lib); ctx=lib.cb_create(); cubiomes_wrap.apply_large_ctx(lib,ctx)
MC=20
def req(s):
    lib.cb_setup(ctx,MC,s)
    a=[ctypes.c_int(0) for _ in range(4)]
    lib.cb_first_stronghold(ctx,MC,s,*[ctypes.byref(x) for x in a])
    sx,sz=a[2].value,a[3].value
    fr=se.get_end_portal_frames(s,sx,sz,MC)
    if fr is None: return None
    return "%d %d %d %s"%(s,sx>>4,sz>>4," ".join("%d %d %d"%f for f in fr[0]))
if __name__=="__main__":
    rnd=random.Random(int(sys.argv[2])); n=int(sys.argv[1])
    for _ in range(n):
        s=rnd.getrandbits(63)-(1<<62)
        r=req(s)
        if r: print(r, flush=True)
