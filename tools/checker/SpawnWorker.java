import java.io.*;
import java.util.*;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.registry.Registry;
import net.minecraft.world.EmptyBlockView;
import nl.jellejurre.seedchecker.SeedChecker;

/**
 * SSA's 1.16 spawn checker (09/10): where does a new player really appear?
 * The real Minecraft 1.16.1 generation (jellejurre's seed-checker) computes the
 * world spawn exactly as MinecraftServer.setupSpawn does (biome search within
 * 256 blocks, 32×32-chunk spiral of columns whose top block is the biome's top
 * material). When nothing is found the spawn stays at the fallback point
 * (chunk centre, y = 64) — inside the terrain. The joining player then tries
 * the 21×21 columns around it WITHOUT the grass rule (sand, red sand… count);
 * when none fits he stays at (8.5, 65, 8.5): in a cave, in lava, in water,
 * in a structure or stuck in the stone.
 *
 *   in : one seed per line ("QUIT" ends)
 *   out: RES seed fallback sx sy sz py feet head covered|sky skyY around ms
 *            mode free blocked px pz stuck roofed roofs rx ry rz feetT headT scan
 *        mode = stay (no candidate column: the player stays at x+0.5, y+1, z+0.5,
 *        inside the terrain — nothing moves him up), random (one of `free`
 *        candidate columns, chosen at random: px pz = the nearest one), blocked
 *        (candidates, all colliding: the last one tried); stuck = 1 if his feet
 *        or head are in a solid block; around = the non-trivial blocks within 2
 *        blocks of him (comma list, "-"); roofed = the free candidate columns
 *        with something other than sky or a tree above the head (roofs = those
 *        blocks, rx ry rz = the first such column); feetT / headT = what the
 *        feet / head are in on the colliding columns ("name:count,…", "-");
 *        scan = full, or partial (fallback point: stopped at the first free
 *        column, nearest first — the counts are then lower bounds)
 *        ERR seed message
 * "READY" is printed once Minecraft is loaded.
 */
public class SpawnWorker {
    static String id(Block b) { return Registry.BLOCK.getId(b).getPath(); }

    static boolean collides(SeedChecker c, int x, int y, int z) {
        BlockState s = c.getBlockState(x, y, z);
        return !s.getCollisionShape(EmptyBlockView.INSTANCE, new BlockPos(x, y, z)).isEmpty();
    }

    /** "name:count,…" most frequent first, "-" when empty. */
    static String tally(Map<String, Integer> m) {
        if (m.isEmpty()) return "-";
        List<Map.Entry<String, Integer>> l = new ArrayList<>(m.entrySet());
        l.sort((p, q) -> q.getValue() - p.getValue());
        StringBuilder b = new StringBuilder();
        for (Map.Entry<String, Integer> e : l) {
            if (b.length() > 0) b.append(',');
            b.append(e.getKey()).append(':').append(e.getValue());
        }
        return b.toString();
    }

    static java.lang.reflect.Field fGen, fWorld;
    static java.lang.reflect.Method mFind;

    /** The seed-checker's SeedChunkGenerator (private) and its copy of
     *  SpawnLocating.findOverworldSpawn, which builds the chunks it needs. */
    static Object genOf(SeedChecker c) throws Exception {
        if (fGen == null) {
            fGen = SeedChecker.class.getDeclaredField("seedChunkGenerator");
            fGen.setAccessible(true);
        }
        Object g = fGen.get(c);
        if (mFind == null) {
            fWorld = g.getClass().getDeclaredField("fakeServerWorld");
            fWorld.setAccessible(true);
            mFind = g.getClass().getDeclaredMethod("findOverworldSpawn", net.minecraft.world.World.class,
                    int.class, int.class, boolean.class);
            mFind.setAccessible(true);
        }
        return g;
    }

    /** the 441 offsets of moveToSpawn, nearest to the spawn first */
    static final int[][] ORDER;
    static {
        List<int[]> l = new ArrayList<>();
        for (int dz = -10; dz <= 10; dz++) for (int dx = -10; dx <= 10; dx++) l.add(new int[]{dx, dz});
        l.sort((p, q) -> (p[0] * p[0] + p[1] * p[1]) - (q[0] * q[0] + q[1] * q[1]));
        ORDER = l.toArray(new int[0][]);
    }

    static java.lang.reflect.Field fChunks;

    /** SeedChecker.clearMemory without its forced full GC (~0.4 s a seed): the
     *  chunk map is emptied, the collector frees it when it needs to. */
    static void freeChunks(SeedChecker c) {
        try {
            Object g = genOf(c);
            if (fChunks == null) {
                fChunks = g.getClass().getDeclaredField("chunkMap");
                fChunks.setAccessible(true);
            }
            ((Map<?, ?>) fChunks.get(g)).clear();
        } catch (Exception e) {
            c.clearMemory();
        }
    }

    /** "FEAT seed n  k a b c  k a b c …" → "FRES seed n  r r …": for each feature
     *  k = 0 dungeon (a b c = the spawner x y z) → r = 1 if a spawner is there,
     *  k = 1 lava lake (a b c = chunk x, chunk z, feature index) → r = the
     *  share of its predicted lava cells (‰) that are lava in Minecraft (the
     *  same shape as SSA's C code, LakeFeature 1.16.1). One world per line. */
    static String feat(String line) {
        String[] p = line.trim().split("\\s+");
        long seed = Long.parseLong(p[1]);
        int n = Integer.parseInt(p[2]);
        StringBuilder out = new StringBuilder("FRES " + seed + " " + n);
        try {
            SeedChecker c = new SeedChecker(seed);
            noStrongholds(c);
            for (int i = 0; i < n; i++) {
                int k = Integer.parseInt(p[3 + 4 * i]);
                int a = Integer.parseInt(p[4 + 4 * i]), b = Integer.parseInt(p[5 + 4 * i]), d = Integer.parseInt(p[6 + 4 * i]);
                if (k == 0) {
                    out.append(' ').append(id(c.getBlock(a, b, d)).equals("spawner") ? 1 : 0);
                } else {
                    int[] top = new int[1];
                    List<int[]> cells = lakeCells(seed, a, b, d, c, top);
                    int lava = 0;
                    for (int[] q : cells) if (id(c.getBlock(q[0], q[1], q[2])).equals("lava")) lava++;
                    // "‰:Y" — Y = the lake's real top lava layer (after its drop)
                    out.append(' ').append(cells.isEmpty() ? 0 : 1000 * lava / cells.size())
                       .append(':').append(top[0]);
                }
            }
            freeChunks(c);
        } catch (Throwable e) {
            return "ERR " + seed + " " + e.getClass().getSimpleName() + ": " + e.getMessage();
        }
        return out.toString();
    }

    /** the lava cells LakeFeature 1.16.1 places for the lava lake of chunk
     *  (cx, cz) with feature index idx. (10/10) With its drop, as the game:
     *  the origin goes down while it is air (surface lakes fall to the ground,
     *  a lake started in a cave to its floor) — read in the generated world
     *  at the box corner, a column the lake never changes. top[0] = Y of the
     *  top lava layer. */
    static List<int[]> lakeCells(long seed, int cx, int cz, int idx, SeedChecker c, int[] top) {
        List<int[]> out = new ArrayList<>();
        net.minecraft.world.gen.ChunkRandom r = new net.minecraft.world.gen.ChunkRandom();
        long pop = r.setPopulationSeed(seed, cx * 16, cz * 16);
        r.setDecoratorSeed(pop, idx, 1);
        if (r.nextInt(8) != 0) return out;
        int x = r.nextInt(16) + cx * 16, z = r.nextInt(16) + cz * 16, y = r.nextInt(r.nextInt(248) + 8);
        if (!(y < 63 || r.nextInt(10) == 0) || y <= 4) return out;
        if (c != null)
            while (y > 5 && c.getBlockState(x, y, z).isAir()) y--;
        if (y <= 4) return out;
        if (top != null) top[0] = y - 1;
        boolean[] sh = new boolean[2048];
        int nb = r.nextInt(4) + 4;
        for (int i = 0; i < nb; i++) {
            double d = r.nextDouble() * 6 + 3, e = r.nextDouble() * 4 + 2, f = r.nextDouble() * 6 + 3;
            double g = r.nextDouble() * (16 - d - 2) + 1 + d / 2, h = r.nextDouble() * (8 - e - 4) + 2 + e / 2,
                   k = r.nextDouble() * (16 - f - 2) + 1 + f / 2;
            for (int a = 1; a < 15; a++) for (int b = 1; b < 15; b++) for (int cc = 1; cc < 7; cc++) {
                double u = (a - g) / (d / 2), v = (cc - h) / (e / 2), w = (b - k) / (f / 2);
                if (u * u + v * v + w * w < 1) sh[(a * 16 + b) * 8 + cc] = true;
            }
        }
        for (int a = 0; a < 16; a++) for (int b = 0; b < 16; b++) for (int cc = 0; cc < 4; cc++)
            if (sh[(a * 16 + b) * 8 + cc]) out.add(new int[]{x + a, y - 4 + cc, z + b});
        return out;
    }

    static java.lang.reflect.Field fCg, fSh;

    /** The first structure-start test of a world computes its 128 stronghold
     *  positions (ChunkGenerator.method_28509: 128 locateBiome) — ~1.5 s of a
     *  seed, ~45 % of the check. No stronghold can start near the spawn: the
     *  first ring is at least ~81 chunks from (0, 0) (4·32 − 1.25·32 chunks,
     *  then at most 112 blocks of biome search), the chunks built here are
     *  within ~45 chunks. The list is filled with one far position, so the
     *  generator thinks it is computed and finds no stronghold here — exactly
     *  what the real list gives. Only in this worker (the portal checker needs
     *  the strongholds). */
    static void noStrongholds(SeedChecker c) throws Exception {
        Object g = genOf(c);
        if (fCg == null) {
            fCg = g.getClass().getDeclaredField("chunkGenerator");
            fCg.setAccessible(true);
            fSh = net.minecraft.world.gen.chunk.ChunkGenerator.class.getDeclaredField("field_24749");
            fSh.setAccessible(true);
        }
        net.minecraft.server.world.ServerWorld w = (net.minecraft.server.world.ServerWorld) fWorld.get(g);
        for (Object cg : new Object[]{fCg.get(g), w.getChunkManager().getChunkGenerator()}) {
            @SuppressWarnings("unchecked")
            List<net.minecraft.util.math.ChunkPos> l = (List<net.minecraft.util.math.ChunkPos>) fSh.get(cg);
            if (l.isEmpty()) l.add(new net.minecraft.util.math.ChunkPos(1_000_000, 1_000_000));
        }
    }

    static java.lang.reflect.Method mInChunk;

    /** SeedChunkGenerator.getSpawnPos (= MinecraftServer.setupSpawn 1.16.1), the
     *  same spiral and the same per-chunk search, but a chunk is skipped when
     *  none of the biome cells its columns can read (horizontal Voronoi: the
     *  1:4 cells x*4-1 .. x*4+4) has a grass / podzol top: there every column
     *  returns null at once in findOverworldSpawn (its first test) — before,
     *  1024 chunks × 256 columns each sampled their biome: ~4 s of the ~5 s a
     *  fallback seed took. Identical result (tested against getSpawnPos). */
    static BlockPos fastSpawnPos(SeedChecker c, long seed) throws Exception {
        Object g = genOf(c);
        net.minecraft.server.world.ServerWorld w = (net.minecraft.server.world.ServerWorld) fWorld.get(g);
        net.minecraft.world.gen.chunk.ChunkGenerator cg = w.getChunkManager().getChunkGenerator();
        net.minecraft.world.biome.source.BiomeSource bs = cg.getBiomeSource();
        List<net.minecraft.world.biome.Biome> list = bs.getSpawnBiomes();
        Random random = new Random(seed);
        BlockPos blockPos = bs.locateBiome(0, w.getSeaLevel(), 0, 256, list, random);
        net.minecraft.util.math.ChunkPos cp = blockPos == null ? new net.minecraft.util.math.ChunkPos(0, 0)
                : new net.minecraft.util.math.ChunkPos(blockPos);
        boolean bl4 = false;
        for (Block b : net.minecraft.tag.BlockTags.VALID_SPAWN.values())
            if (bs.getTopMaterials().contains(b.getDefaultState())) { bl4 = true; break; }
        BlockPos spawnpos = new BlockPos(cp.getCenterBlockPos().add(8, cg.getSpawnHeight(), 8));
        if (mInChunk == null) {
            mInChunk = g.getClass().getMethod("getSpawnPosInChunk", net.minecraft.server.world.ServerWorld.class,
                    net.minecraft.util.math.ChunkPos.class, boolean.class);
        }
        // valid-top flags of the 1:4 cells of the spiral's area, computed on demand
        int ox = (cp.x - 17) * 4, oz = (cp.z - 17) * 4, W = 35 * 4;
        byte[] cell = new byte[W * W];               // 0 unknown, 1 no, 2 valid top
        int i = 0, j = 0, k = 0, l = -1;
        for (int n = 0; n < 1024; ++n) {
            if (i > -16 && i <= 16 && j > -16 && j <= 16) {
                int cx = cp.x + i, cz = cp.z + j;
                boolean maybe = !bl4;
                for (int bx = cx * 4 - 1; bx <= cx * 4 + 4 && !maybe; bx++)
                    for (int bz = cz * 4 - 1; bz <= cz * 4 + 4 && !maybe; bz++) {
                        int idx = (bx - ox) * W + (bz - oz);
                        if (cell[idx] == 0) {
                            net.minecraft.world.biome.Biome b = bs.getBiomeForNoiseGen(bx, 0, bz);
                            cell[idx] = (byte) (b.getSurfaceConfig().getTopMaterial()
                                    .isIn(net.minecraft.tag.BlockTags.VALID_SPAWN) ? 2 : 1);
                        }
                        if (cell[idx] == 2) maybe = true;
                    }
                if (maybe) {
                    BlockPos p2 = (BlockPos) mInChunk.invoke(g, w, new net.minecraft.util.math.ChunkPos(cx, cz), bl4);
                    if (p2 != null) { spawnpos = p2; break; }
                }
            }
            if (i == j || i < 0 && i == -j || i > 0 && i == 1 - j) { int o = k; k = -l; l = o; }
            i += k;
            j += l;
        }
        return spawnpos;
    }

    static BlockPos findSpawn(Object g, int x, int z) throws Exception {
        return (BlockPos) mFind.invoke(g, fWorld.get(g), x, z, false);
    }

    public static void main(String[] a) throws Exception {
        // seed-checker override (tools/checker/overrides): the chunks 2+ away from
        // a chunk whose features are built stay at STRUCTURE_STARTS (vanilla) —
        // set before the generator class loads
        System.setProperty("ssa.fastRegion", "true");
        BufferedReader br = new BufferedReader(new InputStreamReader(System.in));
        new SeedChecker(0L).getBlock(0, 64, 0);          // load Minecraft once
        System.out.println("READY");
        System.out.flush();
        String line;
        while ((line = br.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) continue;
            if (line.equals("QUIT")) break;
            if (line.startsWith("FEAT ")) {          // (10/10) 1.16 dungeons / lava lakes
                System.out.println(feat(line));
                System.out.flush();
                continue;
            }
            if (line.startsWith("CMP ")) {           // self-test: fast vs original spawn
                long sd = Long.parseLong(line.substring(4).trim());
                SeedChecker c1 = new SeedChecker(sd);
                long t0 = System.currentTimeMillis();
                BlockPos a1 = fastSpawnPos(c1, sd);
                long t1 = System.currentTimeMillis();
                BlockPos a2 = new SeedChecker(sd).getSpawnPos();
                long t2 = System.currentTimeMillis();
                System.out.println("CMP " + sd + " " + (a1.equals(a2) ? "SAME" : "DIFF " + a1 + " " + a2)
                        + " " + (t1 - t0) + "ms " + (t2 - t1) + "ms");
                System.out.flush();
                continue;
            }
            long seed;
            try { seed = Long.parseLong(line); } catch (Exception e) {
                System.out.println("ERR 0 bad request"); System.out.flush(); continue; }
            long t = System.currentTimeMillis();
            try {
                SeedChecker c = new SeedChecker(seed);
                noStrongholds(c);
                BlockPos sp = fastSpawnPos(c, seed);
                int x = sp.getX(), y = sp.getY(), z = sp.getZ();
                String below = id(c.getBlock(x, y - 1, z));
                boolean fb = (x & 15) == 8 && (z & 15) == 8 && y == 64
                        && !below.equals("grass_block") && !below.equals("podzol");
                // ServerPlayerEntity.moveToSpawn (1.16.1, read in the bytecode): the
                // 21x21 columns around the spawn in a random cyclic order (step 17),
                // SpawnLocating.findOverworldSpawn(world, x, z, false) — WITHOUT the
                // grass/podzol rule — the first one where the player does not collide
                // wins. None found: the player stays where PlayerEntity put him,
                // (x + 0.5, spawn y + 1, z + 0.5), and nothing moves him up.
                Object gen = genOf(c);
                int free = 0, blocked = 0;
                int fx = 0, fy = 0, fz = 0;          // one free candidate (the nearest)
                long best = Long.MAX_VALUE;
                int roofed = 0, rx0 = Integer.MIN_VALUE, ry0 = 0, rz0 = 0;
                Set<String> roofs = new TreeSet<>();
                Map<String, Integer> feetT = new HashMap<>(), headT = new HashMap<>();
                // nearest columns first: at the fallback point one free column is
                // enough (an ordinary spawn, on the surface) — the scan stops there
                // and the chunks farther away are never generated
                boolean partial = false;
                for (int[] dd : ORDER) {
                        int dx = dd[0], dz = dd[1];
                        if (fb && free > 0) { partial = true; break; }
                        BlockPos q = findSpawn(gen, x + dx, z + dz);
                        if (q == null) continue;
                        if (collides(c, q.getX(), q.getY(), q.getZ())
                                || collides(c, q.getX(), q.getY() + 1, q.getZ())) {
                            blocked++;
                            // where he would be when no column is free (the last one tried,
                            // at random): what his feet / head are in, column by column
                            feetT.merge(id(c.getBlock(q.getX(), q.getY(), q.getZ())), 1, Integer::sum);
                            headT.merge(id(c.getBlock(q.getX(), q.getY() + 1, q.getZ())), 1, Integer::sum);
                            continue;
                        }
                        free++;
                        // what is above his head there: open sky, a tree, or something
                        // else (structure, overhang, cave roof) — the odd spawns
                        int ry = q.getY() + 2;
                        // plants, torches, snow layers… do not make a roof: the first block
                        // that blocks movement does
                        while (ry < 256 && !c.getBlockState(q.getX(), ry, q.getZ()).getMaterial().blocksMovement()) ry++;
                        if (ry < 256) {
                            String rb = id(c.getBlock(q.getX(), ry, q.getZ()));
                            if (!(rb.endsWith("leaves") || rb.endsWith("_log") || rb.equals("vine")
                                    || rb.equals("bee_nest") || rb.equals("cocoa") || rb.endsWith("mushroom_block")
                                    || rb.equals("mushroom_stem"))) {
                                roofed++;
                                roofs.add(rb);
                                if (rx0 == Integer.MIN_VALUE) { rx0 = q.getX(); ry0 = q.getY(); rz0 = q.getZ(); }
                            }
                        }
                        long d = (long) dx * dx + (long) dz * dz;
                        if (d < best) { best = d; fx = q.getX(); fy = q.getY(); fz = q.getZ(); }
                }
                int px = x, py = y + 1, pz = z;
                String mode = "stay";
                if (free > 0) { px = fx; py = fy; pz = fz; mode = "random"; }
                else if (blocked > 0) mode = "blocked";
                int sky = py + 2;
                while (sky < 256 && c.getBlockState(px, sky, pz).isAir()) sky++;
                Set<String> around = new TreeSet<>();
                for (int ex = -2; ex <= 2; ex++)
                    for (int ez = -2; ez <= 2; ez++)
                        for (int ey = -1; ey <= 3; ey++) {
                            String n = id(c.getBlock(px + ex, py + ey, pz + ez));
                            if (!n.endsWith("air") && !n.equals("stone") && !n.equals("dirt"))
                                around.add(n);
                        }
                boolean stuck = collides(c, px, py, pz) || collides(c, px, py + 1, pz);
                System.out.println("RES " + seed + " " + (fb ? 1 : 0) + " " + x + " " + y + " " + z
                        + " " + py + " " + id(c.getBlock(px, py, pz)) + " " + id(c.getBlock(px, py + 1, pz))
                        + " " + (sky < 256 ? "covered" : "sky") + " " + sky
                        + " " + (around.isEmpty() ? "-" : String.join(",", around))
                        + " " + (System.currentTimeMillis() - t)
                        + " " + mode + " " + free + " " + blocked + " " + px + " " + pz + " " + (stuck ? 1 : 0)
                        + " " + roofed + " " + (roofs.isEmpty() ? "-" : String.join(",", roofs))
                        + " " + (rx0 == Integer.MIN_VALUE ? "0 0 0" : rx0 + " " + ry0 + " " + rz0)
                        + " " + tally(feetT) + " " + tally(headT) + " " + (partial ? "partial" : "full"));
                freeChunks(c);
            } catch (Throwable e) {
                System.out.println("ERR " + seed + " " + String.valueOf(e).replace('\n', ' '));
            }
            System.out.flush();
        }
    }
}
