import lakes, cubiomes_wrap, collections
cl=cubiomes_wrap.make_worker_lib()
res={}
for l in open('bout_00.txt').read().split('\n')+open('bout_01.txt').read().split('\n'):
    p=l.split()
    if p and p[0]=='RES': res[int(p[1])]=int(p[2])
rows=[]
for line in open('bhits.txt'):
    req=line.split('#')[0].split(); p=list(map(int,req)); ws=p[0]
    fr=[tuple(p[3+3*i:6+3*i]) for i in range(12)]
    cl.setup_and_seed(20,ws,False); ba=lambda cx,cz: cl.get_biome_at(cx*16+8,cz*16+8,4)
    cs=set((x>>4,z>>4) for x,_,z in fr)
    ring=set((a+dx,b+dz) for a,b in cs for dx in (-1,0,1) for dz in (-1,0,1))
    for cx,cz in sorted(ring):
        for kind,x,y,z,r in lakes.lakes_in_chunk(ws,cx,cz,ba(cx,cz)):
            y0=y-4; b=lakes.lake_shape(r); bo=lakes.border(b)
            us=[(fy-y0) for fx,fy,fz in fr if (fx-x,fy-y0,fz-z) in b]
            ub=[(fy-y0) for fx,fy,fz in fr if (fx-x,fy-y0,fz-z) in bo]
            if not us and not (kind=='lava' and ub): continue
            rows.append((res.get(ws,-1),ws,kind,y,tuple(sorted(set(us))),len(us),tuple(sorted(set(ub))),len(ub)))
pos=[r for r in rows if r[0]>0]; neg=[r for r in rows if r[0]==0]
print(len(rows),len(pos),len(neg))
for r in pos: print(r)
c=collections.Counter((r[2],r[4]) for r in neg); print(c.most_common(20))
def rule(r, k):
    kind,us,ub=r[2],r[4],r[6]
    if k==0: return (us and max(us)>=6) or (kind=='lava' and ub and max(ub)>=6)
    if k==1: return (us and max(us)>=6) or (kind=='lava' and ub and (max(ub)>=6 or min(ub)<=3))
for k in (0,1):
    sel=[r for r in rows if rule(r,k)]; 
    seeds=set(r[1] for r in sel); P=set(r[1] for r in pos)
    print(k, 'selected seeds',len(seeds),'true',len(seeds&P),'missed',[ (r[1],r[0]) for r in pos if r[1] not in seeds])
