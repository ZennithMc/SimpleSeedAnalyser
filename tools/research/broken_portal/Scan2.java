import java.io.*; import java.util.*; import java.lang.reflect.Field;
import net.minecraft.block.*; import net.minecraft.structure.*; import net.minecraft.util.math.*;
import net.minecraft.util.registry.Registry;
import net.minecraft.world.chunk.Chunk; import net.minecraft.world.gen.chunk.ChunkGenerator;
import net.minecraft.world.gen.feature.StructureFeature;
import nl.jellejurre.seedchecker.*;
/** in: seed scx scz x1 y1 z1 ... (12)
 *  out: RES seed total [cx cz order block ruinedRef n]...  (order: F = frame chunk, R = ring) | MISS seed why
 *  Frame chunks decorated first, one by one, then the ring (radius 1) in a fixed order;
 *  after every decoration the frames are re-read and the newly broken ones attributed. */
public class Scan2 {
  static Field fGen, fCG, fList;
  @SuppressWarnings("unchecked")
  static void pin(SeedChecker c, int sx, int sz) throws Exception {
    if (fGen==null){ fGen=SeedChecker.class.getDeclaredField("seedChunkGenerator"); fGen.setAccessible(true);
      fCG=fGen.get(c).getClass().getDeclaredField("chunkGenerator"); fCG.setAccessible(true);
      fList=ChunkGenerator.class.getDeclaredField("field_24749"); fList.setAccessible(true);}
    List<ChunkPos> l=(List<ChunkPos>)fList.get(fCG.get(fGen.get(c))); l.clear(); l.add(new ChunkPos(sx,sz));
  }
  static String room(SeedChecker c,int sx,int sz,int[][] f){
    StructureStart<?> st=null;
    for(Map.Entry<StructureFeature<?>,StructureStart<?>> e: c.getOrBuildChunk(sx,sz,1).getStructureStarts().entrySet())
      if(e.getKey()==StructureFeature.STRONGHOLD&&e.getValue()!=null&&e.getValue().hasChildren()) st=e.getValue();
    if(st==null) return "nostronghold";
    for(StructurePiece p: st.getChildren()) if(p.getClass().getSimpleName().equals("PortalRoom")){
      for(int[] q:f) if(!p.getBoundingBox().contains(new BlockPos(q[0],q[1],q[2]))) return "outside"; return null;}
    return "noroom";
  }
  static int K=5;
  public static void main(String[] a) throws Exception {
    if(a.length>0) K=Integer.parseInt(a[0]);
    PrintStream out=new PrintStream(new FileOutputStream(FileDescriptor.out),true,"UTF-8");
    System.setOut(new PrintStream(new OutputStream(){public void write(int b){}}));
    out.println("READY");
    BufferedReader br=new BufferedReader(new InputStreamReader(System.in)); String line;
    while((line=br.readLine())!=null){
      String[] p=line.trim().split("\\s+"); if(p.length<5) { if(line.trim().equals("QUIT")) break; continue; }
      long seed=Long.parseLong(p[0]); int sx=Integer.parseInt(p[1]), sz=Integer.parseInt(p[2]);
      int n=(p.length-3)/3; int[][] f=new int[n][3];
      for(int i=0;i<n;i++)for(int j=0;j<3;j++) f[i][j]=Integer.parseInt(p[3+3*i+j]);
      try{
        long t=System.currentTimeMillis();
        SeedChecker c=new SeedChecker(seed,TargetState.STRUCTURES,SeedCheckerDimension.OVERWORLD);
        pin(c,sx,sz);
        String why=room(c,sx,sz,f);
        if(why!=null){ out.println("MISS "+seed+" "+why); c.clearMemory(); continue; }
        LinkedHashSet<Long> F=new LinkedHashSet<>(); 
        for(int[] q:f) F.add((((long)(q[0]>>4))<<32)|((q[2]>>4)&0xFFFFFFFFL));
        TreeSet<Long> R=new TreeSet<>();
        for(long k:F){int cx=(int)(k>>32),cz=(int)k; for(int dx=-1;dx<=1;dx++)for(int dz=-1;dz<=1;dz++){long kk=(((long)(cx+dx))<<32)|((cz+dz)&0xFFFFFFFFL); if(!F.contains(kk)) R.add(kk);}}
        c.clearMemory();
        StringBuilder all=new StringBuilder(); int best=0;
        for(int oi=0;oi<K;oi++){
        List<Long> order=new ArrayList<>(F); order.addAll(R);
        if(oi>0){ Collections.shuffle(order,new Random(seed*31+oi)); }
        c=new SeedChecker(seed,TargetState.STRUCTURES,SeedCheckerDimension.OVERWORLD); pin(c,sx,sz);
        boolean[] gone=new boolean[n]; int total=0; StringBuilder sb=new StringBuilder();
        for(int i=0;i<order.size();i++){
          long k=order.get(i); int cx=(int)(k>>32),cz=(int)k;
          Chunk ch=c.getOrBuildChunk(cx,cz,8);
          Map<String,Integer> nb=new TreeMap<>();
          for(int j=0;j<n;j++){ if(gone[j]) continue;
            if(c.getBlock(f[j][0],f[j][1],f[j][2])!=Blocks.END_PORTAL_FRAME){ gone[j]=true; total++;
              nb.merge(Registry.BLOCK.getId(c.getBlock(f[j][0],f[j][1],f[j][2])).getPath(),1,Integer::sum);} }
          if(!nb.isEmpty()){
            boolean rr=!ch.getStructureReferences(StructureFeature.RUINED_PORTAL).isEmpty();
            for(Map.Entry<String,Integer> e:nb.entrySet()) sb.append(',').append(cx).append(':').append(cz).append(':').append(e.getKey()).append(':').append(rr?1:0).append(':').append(e.getValue());
          }
        }
        c.clearMemory(); best=Math.max(best,total);
        all.append(" o").append(oi).append('=').append(total).append(sb);
        }
        out.println("RES "+seed+" "+best+" "+(System.currentTimeMillis()-t)+all);
      }catch(Throwable e){ out.println("ERR "+seed+" "+String.valueOf(e).replace('\n',' ')); }
    }
  }
}
