import java.io.*;
import java.lang.reflect.Field;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import net.minecraft.structure.StructurePiece;
import net.minecraft.structure.StructureStart;
import net.minecraft.util.math.BlockBox;
import net.minecraft.util.math.ChunkPos;
import net.minecraft.world.gen.feature.StructureFeature;
import net.minecraft.block.Block;
import net.minecraft.block.Blocks;
import net.minecraft.util.registry.Registry;
import nl.jellejurre.seedchecker.SeedChecker;
import nl.jellejurre.seedchecker.SeedCheckerDimension;
import nl.jellejurre.seedchecker.TargetState;

/**
 * (The same source compiles against Minecraft 1.16.1 and 1.18.1 — 03/10.)
 * SSA's link to the real Minecraft 1.16.1 world generation (jellejurre's
 * seed-checker): « is this end portal really broken by the ruined portal? »
 * One request per line on stdin, one answer per line on stdout, so SSA runs
 * several workers side by side and keeps them warm (02/10). No Gradle, no files.
 *
 *   in : [N|A] [S:scx,scz] seed rox roz x1 y1 z1 ... x12 y12 z12
 *        rox roz = chunk of the ruined portal's origin; then the 12 exact frame
 *        positions. Without N only the « direct /tp onto the portal » world is
 *        built: the frame chunks generate FIRST, then the ruined-portal chunk and
 *        its ring — that order is what breaks frames in game (the ruined piece
 *        spreads netherrack / lava over chunks already generated). With N the
 *        default (natural) order is checked too; with A only when the /tp world
 *        breaks at least one frame (the natural order never breaks more).
 *        S:scx,scz = the stronghold's start chunk as SSA (cubiomes) computed it.
 *        Minecraft is then told the stronghold is THERE (the list of stronghold
 *        chunks is pinned), because the stronghold search differs between
 *        1.16.1 and 1.16.2+ (bamboo jungles, MC-199298): without it the checker
 *        may look at a place where its own 1.16.1 world has no portal room (03/10).
 *        The 12 positions must then lie inside that stronghold's portal room,
 *        otherwise the answer is MISS (never « 12 broken » by mistake).
 *   out: RES seed forced natural ms [x y z block]...   (natural = -1 if not asked)
 *        MISS seed reason
 *        ERR seed message
 * "READY" is printed once Minecraft is loaded (a few seconds).
 */
public class SsaChecker {
    static String id(Block b) { return Registry.BLOCK.getId(b).toString(); }

    /** Frame chunks first (except the ruined trigger chunk), then the trigger
     *  chunk and its ring last (same as the original CheckerServer). */
    static Field fGen, fChunkGen, fList;

    /** Pins Minecraft's stronghold list to the given chunk (see the header). */
    @SuppressWarnings("unchecked")
    static void pinStronghold(SeedChecker c, int[] sh) throws Exception {
        if (sh == null) return;
        if (fGen == null) {
            fGen = SeedChecker.class.getDeclaredField("seedChunkGenerator");
            fGen.setAccessible(true);
            Object scg = fGen.get(c);
            fChunkGen = scg.getClass().getDeclaredField("chunkGenerator");
            fChunkGen.setAccessible(true);
            // the stronghold chunk list: ChunkGenerator.field_24749 (1.16 yarn) /
            // .strongholds (1.18 yarn); the class moved between versions too
            for (Class<?> k = fChunkGen.get(scg).getClass(); k != null && fList == null; k = k.getSuperclass())
                for (String n : new String[] { "field_24749", "strongholds" })
                    try { fList = k.getDeclaredField(n); } catch (NoSuchFieldException ignored) { }
            if (fList == null) throw new NoSuchFieldException("stronghold list");
            fList.setAccessible(true);
        }
        Object gen = fChunkGen.get(fGen.get(c));
        List<ChunkPos> l = (List<ChunkPos>) fList.get(gen);
        l.clear();
        l.add(new ChunkPos(sh[0], sh[1]));
    }

    /** null if the 12 positions are inside the portal room of the stronghold that
     *  starts in chunk sh, else the reason. */
    static String portalRoomCheck(SeedChecker c, int[] sh, int[][] frames) {
        if (sh == null) return null;
        StructureStart<?> st = null;
        for (Map.Entry<StructureFeature<?>, StructureStart<?>> e :
                c.getOrBuildChunk(sh[0], sh[1], 1).getStructureStarts().entrySet())
            if (e.getKey() == StructureFeature.STRONGHOLD && e.getValue() != null && e.getValue().hasChildren())
                st = e.getValue();
        if (st == null) return "no-stronghold-at-" + sh[0] + "," + sh[1];
        for (StructurePiece pc : st.getChildren()) {
            if (!pc.getClass().getSimpleName().equals("PortalRoom")) continue;
            BlockBox b = pc.getBoundingBox();
            boolean all = true;
            for (int[] q : frames) if (!b.contains(new net.minecraft.util.math.BlockPos(q[0], q[1], q[2]))) all = false;
            if (all) return null;
            return "frames-outside-portal-room " + String.valueOf(b).replace(' ', '_');
        }
        return "no-portal-room";
    }

    static SeedChecker forcedWorld(long seed, int[][] frames, int rox, int roz, int[] sh) throws Exception {
        SeedChecker c = new SeedChecker(seed, TargetState.STRUCTURES, SeedCheckerDimension.OVERWORLD);
        pinStronghold(c, sh);
        LinkedHashSet<Long> fcs = new LinkedHashSet<>();
        for (int[] f : frames)
            fcs.add((((long) (f[0] >> 4)) << 32) | ((f[2] >> 4) & 0xFFFFFFFFL));
        for (long key : fcs) {
            int cx = (int) (key >> 32), cz = (int) key;
            if (cx == rox && cz == roz) continue;
            c.getOrBuildChunk(cx, cz, 8);
        }
        for (int dx = -1; dx <= 1; dx++)
            for (int dz = -1; dz <= 1; dz++)
                c.getOrBuildChunk(rox + dx, roz + dz, 8);
        return c;
    }

    public static void main(String[] args) throws Exception {
        PrintStream out = new PrintStream(new FileOutputStream(FileDescriptor.out), true, "UTF-8");
        System.setOut(new PrintStream(new OutputStream() { public void write(int b) {} }));  // MC chatter
        SeedChecker warm = new SeedChecker(0L, TargetState.NO_STRUCTURES, SeedCheckerDimension.OVERWORLD);
        warm.getOrBuildChunk(0, 0, 1);
        warm.clearMemory();
        out.println("READY");
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in, "UTF-8"));
        String line;
        while ((line = br.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) continue;
            if (line.equals("QUIT")) break;
            String[] p = line.split("\\s+");
            int k = 0;
            boolean natural = false, adaptive = false;
            if (p[0].equals("N")) { natural = true; k = 1; }
            else if (p[0].equals("A")) { adaptive = true; k = 1; }
            int[] sh = null;
            if (p[k].startsWith("S:")) {
                String[] q = p[k].substring(2).split(",");
                sh = new int[] { Integer.parseInt(q[0]), Integer.parseInt(q[1]) };
                k++;
            }
            long seed = 0;
            try {
                seed = Long.parseLong(p[k]);
                int rox = Integer.parseInt(p[k + 1]), roz = Integer.parseInt(p[k + 2]);
                int n = (p.length - k - 3) / 3;
                int[][] f = new int[n][3];
                for (int i = 0; i < n; i++)
                    for (int j = 0; j < 3; j++) f[i][j] = Integer.parseInt(p[k + 3 + 3 * i + j]);
                long t = System.currentTimeMillis();
                int nat = -1;
                SeedChecker c1 = forcedWorld(seed, f, rox, roz, sh);
                String miss = portalRoomCheck(c1, sh, f);
                if (miss != null) {
                    c1.clearMemory();
                    out.println("MISS " + seed + " " + miss);
                    continue;
                }
                StringBuilder fl = new StringBuilder();
                int forced = 0;
                for (int[] q : f) {
                    Block b = c1.getBlock(q[0], q[1], q[2]);
                    if (b != Blocks.END_PORTAL_FRAME) {
                        forced++;
                        fl.append(' ').append(q[0]).append(' ').append(q[1]).append(' ').append(q[2])
                          .append(' ').append(id(b));
                    }
                }
                c1.clearMemory();
                if (natural || (adaptive && forced > 0)) {
                    SeedChecker c0 = new SeedChecker(seed, TargetState.STRUCTURES, SeedCheckerDimension.OVERWORLD);
                    pinStronghold(c0, sh);
                    nat = 0;
                    for (int[] q : f) if (c0.getBlock(q[0], q[1], q[2]) != Blocks.END_PORTAL_FRAME) nat++;
                    c0.clearMemory();
                }
                out.println("RES " + seed + " " + forced + " " + nat + " " + (System.currentTimeMillis() - t) + fl);
            } catch (Throwable e) {
                out.println("ERR " + seed + " " + String.valueOf(e).replace('\n', ' '));
            }
        }
    }
}
