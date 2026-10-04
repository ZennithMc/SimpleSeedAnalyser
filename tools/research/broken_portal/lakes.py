"""1.16 lake predictor (water + lava) — exact RNG of LakeFeature, terrain checks NOT done."""
import sys; sys.path.insert(0,'/home/claude/ssa')
M=(1<<48)-1
class JR:
    def __init__(s,seed): s.s=(seed^0x5DEECE66D)&M
    def nxt(s,b):
        s.s=(s.s*0x5DEECE66D+0xB)&M; r=s.s>>(48-b)
        return r-(1<<32) if b==32 and r>=(1<<31) else r
    def nextInt(s,n):
        if n&(-n)==n: return (n*(s.nxt(31)))>>31
        while True:
            b=s.nxt(31); v=b%n
            if b-v+(n-1)<(1<<31): return v
    def nextLong(s):
        return ((s.nxt(32)<<32)+s.nxt(32)) & 0xFFFFFFFFFFFFFFFF
    def nextDouble(s): return ((s.nxt(26)<<27)+s.nxt(27))*(1.0/(1<<53))
def s64(x):
    x&=0xFFFFFFFFFFFFFFFF; return x-(1<<64) if x>>63 else x
def pop_seed(ws,bx,bz):
    r=JR(ws); a=s64(r.nextLong()|1); b=s64(r.nextLong()|1)
    return s64(bx*a+bz*b ^ ws)
def lake_shape(r):
    bls=set(); n=r.nextInt(4)+4
    for _ in range(n):
        d=r.nextDouble()*6+3; e=r.nextDouble()*4+2; f=r.nextDouble()*6+3
        g=r.nextDouble()*(16-d-2)+1+d/2; h=r.nextDouble()*(8-e-4)+2+e/2; k=r.nextDouble()*(16-f-2)+1+f/2
        for l in range(1,15):
            for m in range(1,15):
                for nn in range(1,7):
                    o=(l-g)/(d/2); p=(nn-h)/(e/2); q=(m-k)/(f/2)
                    if o*o+p*p+q*q<1: bls.add((l,nn,m))
    return bls
DESERT={2,17,130}
def border(bls):
    out=set()
    for (l,n,m) in bls:
        for d in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
            c=(l+d[0],n+d[1],m+d[2])
            if c not in bls and 0<=c[0]<16 and 0<=c[1]<8 and 0<=c[2]<16: out.add(c)
    return out
def cells(kind,r):
    b=lake_shape(r)
    return b|border(b) if kind=='lava' else b
def lakes_in_chunk(ws,cx,cz,biome):
    """[(kind, x, y, z, rng)] ; (x,y,z) = decorator position (before air-drop / down(4))."""
    pop=pop_seed(ws,cx*16,cz*16); out=[]
    idx=0
    if biome not in DESERT:
        r=JR(pop+0+10000)
        if r.nextInt(4)==0:
            x=r.nextInt(16)+cx*16; z=r.nextInt(16)+cz*16; y=r.nextInt(256)
            out.append(('water',x,y,z,r))
        idx=1
    r=JR(pop+idx+10000)
    if r.nextInt(8)==0:
        x=r.nextInt(16)+cx*16; z=r.nextInt(16)+cz*16; y=r.nextInt(r.nextInt(248)+8)
        if y<63 or r.nextInt(10)==0: out.append(('lava',x,y,z,r))
    return out
def hits(ws,frames,biome_at):
    """lakes whose shape covers a frame assuming no air drop: [(kind,cx,cz,y,nframes)]"""
    fs=set(map(tuple,frames)); cs=set((x>>4,z>>4) for x,_,z in frames)
    ring=set((a+dx,b+dz) for a,b in cs for dx in (-1,0,1) for dz in (-1,0,1))
    res=[]
    for cx,cz in sorted(ring):
        for kind,x,y,z,r in lakes_in_chunk(ws,cx,cz,biome_at(cx,cz)):
            y0=y-4
            if not any(x<=fx<=x+15 and z<=fz<=z+15 and y0<=fy<=y0+7 for fx,fy,fz in fs): continue
            bls=cells(kind,r)
            n=sum(1 for fx,fy,fz in fs if (fx-x,fy-y0,fz-z) in bls)
            res.append((kind,cx,cz,y,n))
    return res
def hits_drop(ws,frames,biome_at,margin=4):
    """lakes whose decorator column falls inside the portal room footprint (frames
    square +margin) with y at/above the frames: after the air drop they may land on
    the room floor. Tries landing tops fy-3..fy+2. [(kind,cx,cz,y,nframes)]"""
    fs=set(map(tuple,frames)); cs=set((x>>4,z>>4) for x,_,z in frames)
    ring=set((a+dx,b+dz) for a,b in cs for dx in (-1,0,1) for dz in (-1,0,1))
    xs=[f[0] for f in frames]; zs=[f[2] for f in frames]; fy=frames[0][1]
    x0,x1,z0,z1=min(xs)-margin,max(xs)+margin,min(zs)-margin,max(zs)+margin
    res=[]
    for cx,cz in sorted(ring):
        for kind,x,y,z,r in lakes_in_chunk(ws,cx,cz,biome_at(cx,cz)):
            if not (x0<=x<=x1 and z0<=z<=z1 and y>=fy-3): continue
            bls=cells(kind,r); best=0
            for top in range(fy-3,min(y,fy+3)+1):
                y0=top-4
                best=max(best,sum(1 for fx,fy2,fz in fs if (fx-x,fy2-y0,fz-z) in bls))
            if best: res.append((kind,cx,cz,y,best))
    return res
