"""tools/servercheck/treecheck.py — (05/11) checks SSA's tree cracker models on a headless
server's world (mkserver.py): a Python reference of the forest tree feature (1.16.1,
1.16.2-1.16.5, 1.17.1) against the trees the game really grew — positions, types,
heights, the 12 random corner leaves — and finds the decoration salt.

    python3 treecheck.py <server dir> <version> [cx,cz ...]

Results (05/11, seed 12345, 25 forest + 9 birch forest chunks): salt 80001 (1.16.x)
and 80002 (1.17.1) for Forest and Birch Forest; positions / types / heights exact;
leaves differ only where another tree's leaf, a later log or the terrain took the
corner. The C engine (cubiomes_src/treecrack_core.h, tc_trees) gives the same trees."""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from piececat import World

from loot_engine import JavaRandom
M64=(1<<64)-1
def s64(v):
    v&=M64; return v-(1<<64) if v>>63 else v

def pop_seed(ws, bx, bz):
    r=JavaRandom(ws); a=r.next_long()|1; b=r.next_long()|1
    return s64(bx*a+bz*b ^ ws)

def trees(ws, cx, cz, ver, salt, generated=None, biome='forest'):
    """ver: '1.16.1' | '1.16.5' | '1.17.1'. generated(x,z,type) -> bool decides whether
    a tree actually grew (callback against the world). Returns list of dicts."""
    r=JavaRandom(s64(pop_seed(ws,cx*16,cz*16)+salt))
    n=10+(1 if r.next_float()<0.1 else 0)
    out=[]
    for i in range(n):
        x=cx*16+r.next_int(16); z=cz*16+r.next_int(16)
        if biome=='birch_forest': t='birch'
        elif r.next_float()<0.2: t='birch'
        elif r.next_float()<0.1: t='fancy'
        else: t='oak'
        d={'x':x,'z':z,'type':t}
        if t in('oak','birch'):
            h=(4 if t=='oak' else 5)+r.next_int(3)+r.next_int(1)
            if ver=='1.16.1': r.next_int(1)          # foliage radius (1.16.1: a random int)
            d['h']=h
            g = generated(x,z,t) if generated else True
            d['gen']=g
            if g:
                if ver=='1.16.1': r.next_int(1)      # foliage offset
                lv=[r.next_int(2) for _ in range(16)]   # top layer first (y offset 0,-1,-2,-3)
                # layers bottom-up: offsets -3,-2,-1 → indices 12..15, 8..11, 4..7
                d['leaves']=''.join(str(lv[k]) for k in list(range(12,16))+list(range(8,12))+list(range(4,8)))
                d['bee']=r.next_float()
        else:
            h=3+r.next_int(12)+r.next_int(1); d['h']=h
            if ver=='1.16.1': r.next_int(1)
            g = generated(x,z,t) if generated else True
            d['gen']=g
            if g and ver=='1.16.1':
                import math
                bc=(0x765543321000>>((h-3)*4))&0xF; blobs=1
                n4=h+2
                for i in range(bc):
                    n2=n4-5-i
                    if n2 < n4*0.3: f=-1.0
                    else:
                        f_=n4/2.0; f2=f_-n2
                        if f2==0: f3=f_
                        elif abs(f2)>=f_: f3=0.0
                        else: f3=math.sqrt(f_*f_-f2*f2)
                        f=f3*0.5
                    import struct
                    fl=lambda v: struct.unpack('f',struct.pack('f',v))[0]
                    d4=1.0*fl(f)*(r.next_float()+0.328)
                    d2=fl(r.next_float()*2.0)*3.141592653589793
                    ex=math.floor(d4*math.sin(d2)+0.5); ez=math.floor(d4*math.cos(d2)+0.5)
                    ey=n2-1; sy=ey-math.sqrt(ex*ex+ez*ez)*0.381
                    if int(sy) >= n4*0.2: blobs+=1
                for _ in range(blobs): r.next_int(1)
                r.next_float()
            elif g:
                bc=(0x765543321000>>((h-3)*4))&0xF
                for _ in range(bc): r.next_float(); r.next_float()
                r.next_float()
        out.append(d)
    return out


LOG={'oak_log':'oak','birch_log':'birch'}
def world_trees(w, cx, cz):
    W=World(w,"")
    out=[]
    for x in range(cx*16,cx*16+16):
        for z in range(cz*16,cz*16+16):
            for y in range(110,50,-1):
                b=W.block(x,y,z)
                if b is None: return None
                b=b.replace('minecraft:','')
                if b in LOG:
                    # descend to base
                    yy=y
                    while W.block(x,yy-1,z).replace('minecraft:','')==b: yy-=1
                    below=W.block(x,yy-1,z).replace('minecraft:','')
                    if below in('dirt','grass_block','podzol','coarse_dirt'):
                        top=yy
                        while W.block(x,top+1,z).replace('minecraft:','')==b: top+=1
                        h=top-yy+1
                        t=LOG[b]
                        lf=b.replace('_log','_leaves')
                        # fancy: logs with horizontal axis near / height >=7 for oak
                        if t=='oak' and h>6: t='fancy'
                        leaves=''
                        if t!='fancy':
                            for ly,r in ((top-2,2),(top-1,2),(top,1)):
                                for dx,dz in((-r,-r),(-r,r),(r,-r),(r,r)):
                                    nb=W.block(x+dx,ly,z+dz).replace('minecraft:','')
                                    leaves+='1' if nb.endswith('_leaves') else ('L' if nb.endswith('_log') else ('0' if nb in ('air','cave_air','grass','tall_grass','fern','large_fern') or 'flower' in nb or nb in ('dandelion','poppy','lily_of_the_valley','cornflower','allium','azure_bluet','oxeye_daisy','rose_bush','lilac','peony') or nb.endswith('tulip') else 'T'))
                        out.append(dict(x=x,z=z,type=t,h=h,leaves=leaves,base=yy))
                    break
                if b not in('air','cave_air','grass','tall_grass','fern') and not b.endswith('leaves') and not b.endswith('_log') and 'flower' not in b and b not in ('vine','snow'):
                    if b in ('grass_block','dirt','stone','sand','water','gravel','podzol','coarse_dirt'): break
    return out


# ── (06/11) taiga: spruce and pine (1.16.1 / 1.16.2+ / 1.17.1) ────────────────
# Checked: 1.16.5 salt 80001 (203/203 trees placed where the world has one), 1.17.1
# 80002, 1.16.1 80001 (its pine draws 1 + nextInt(1) for the radius and an offset
# nextInt(1) when it grows). Trunks exact; crown details exact but where neighbours
# overlap. Spruce: layers above the top log = offset + 1; widest radius = the foliage
# radius when foliage height + offset + 1 + top radius >= 8, else 2. Pine: leaf layers
# = foliage height + 1 - (radius == 1); widest = min(radius, foliage height - 1).

def taiga_trees(ws, cx, cz, ver, salt, generated, v161_pine_offset=True, v161_spruce=True):
    r=JavaRandom(s64(pop_seed(ws,cx*16,cz*16)+salt))
    n=10+(1 if r.next_float()<0.1 else 0)
    out=[]
    for i in range(n):
        x=cx*16+r.next_int(16); z=cz*16+r.next_int(16)
        t='pine' if r.next_float()<0.33333334 else 'spruce'
        d={'x':x,'z':z,'type':t}
        if t=='spruce':
            trunk=5+r.next_int(3)+r.next_int(2)
            ft=1+r.next_int(2); fh=max(4,trunk-ft); rad=2+r.next_int(2)
            g=generated(x,z,t); d['gen']=g
            if g:
                off=r.next_int(3); top=r.next_int(2)
                d.update(trunk=trunk, bare=trunk-fh, rad=rad, off=off, top=top,
                         widest=(rad if fh+off+1+top>=8 else 2))
        else:
            trunk=6+r.next_int(5)+r.next_int(1)
            fh=3+r.next_int(2)
            if ver=='1.16.1': r.next_int(1)
            fr=1+r.next_int(max(trunk-fh+1,1))
            g=generated(x,z,t); d['gen']=g
            if g and ver=='1.16.1' and v161_pine_offset: r.next_int(1)
            d.update(trunk=trunk, fh=fh, fr=fr, layers=fh+1-(fr==1), widest=min(fr,fh-1))
        out.append(d)
    return out

def measure(W, x, z):
    """spruce-log tree at x,z: base, logs, lowest leaf y, highest leaf y, radius per layer"""
    top=None
    for y in range(130,50,-1):
        b=W.block(x,y,z)
        if b is None: return None
        b=b.replace('minecraft:','')
        if b=='spruce_log': top=y; break
        if b not in ('air','spruce_leaves','snow','cave_air'): return None
    if top is None: return None
    base=top
    while W.block(x,base-1,z).replace('minecraft:','')=='spruce_log': base-=1
    below=W.block(x,base-1,z).replace('minecraft:','')
    if below not in ('dirt','grass_block','podzol','coarse_dirt'): return None
    def arm(y,dx,dz):
        k=0
        while k<5 and W.block(x+dx*(k+1),y,z+dz*(k+1)).replace('minecraft:','')=='spruce_leaves': k+=1
        return k
    layers={}
    for y in range(base, top+6):
        c=W.block(x,y,z).replace('minecraft:','')
        arms=[arm(y,1,0),arm(y,-1,0),arm(y,0,1),arm(y,0,-1)]
        if c=='spruce_leaves' or min(arms)>0:
            layers[y]=min(arms)
    return dict(base=base, logs=top-base+1, top=top, layers=layers)


def main():
    d, ver = sys.argv[1], sys.argv[2]
    w = os.path.join(d, "w12345")
    chunks = [tuple(map(int, a.split(","))) for a in sys.argv[3:]] or \
        [(x, z) for x in range(16, 21) for z in range(-2, 3)]
    WT = {c: world_trees(w, *c) for c in chunks}
    best = None
    for salt in list(range(80000, 80006)) + list(range(60000, 60004)):
        hit = 0
        for c in chunks:
            wt = {(t["x"], t["z"]): t for t in WT[c] or []}
            used = set()
            for t in trees(12345, c[0], c[1], ver, salt,
                           lambda x, z, ty: (x, z) in wt and not ((x, z) in used or used.add((x, z)))):
                hit += t["gen"]
        print(f"salt {salt}: {hit} trees placed where the world has one")
        if best is None or hit > best[0]:
            best = (hit, salt)
    print("salt:", best[1])


if __name__ == "__main__":
    main()
