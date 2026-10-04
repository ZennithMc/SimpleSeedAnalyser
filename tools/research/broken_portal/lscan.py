import sys, lakes, cubiomes_wrap
cubiomes_wrap.set_large_biomes(False)
cl=cubiomes_wrap.make_worker_lib()
for fn in sys.argv[1:]:
  for line in open(fn):
    p=list(map(int,line.split())); ws=p[0]; fr=[tuple(p[3+3*i:6+3*i]) for i in range(12)]
    cl.setup_and_seed(20, ws, False)
    ba=lambda cx,cz: cl.get_biome_at(cx*16+8,cz*16+8,4)
    h=lakes.hits(ws,fr,ba)+[("drop",)+t for t in lakes.hits_drop(ws,fr,ba)]
    h=[t for t in h if t[-1]>0]
    if h: print(line.strip(), "#", h, flush=True)
