/* mineshaft116.h — Minecraft Java 1.16.1 abandoned mineshafts, bit-exact.
 *
 * Start placement (carver seed + nextDouble < p, biome = 1:4 biome at
 * ((cx<<2)+2, (cz<<2)+2), MESA type in badlands ids 37-39/165-167), layout
 * (MineshaftRoom / Corridor / Crossing / Stairs, depth <= 8, radius 80, then
 * the vertical shift) and the placement of the pieces during the population of
 * one chunk (step UNDERGROUND_STRUCTURES, structure index 0: the random is
 * setDecoratorSeed(popSeed, 0, 3), shared by every start reaching the chunk,
 * iterated in the order of the chunk's fastutil LongOpenHashSet of references).
 *
 * World access goes through callbacks.  The 1.16.1 code only reads and writes
 * blocks of the populated chunk itself (pieces are clipped to its box, y 1..512),
 * so get() / set() are only called with x in cx*16..cx*16+15, z likewise
 * (set() only for y 1..255).  isUnderSeaLevel() reads the OCEAN_FLOOR_WG
 * heightmap (highest block whose material blocks movement, + 1).  During
 * population the chunk is still at LIQUID_CARVERS, whose tracked heightmaps are
 * the post-carver ones, so OCEAN_FLOOR_WG stays frozen at its LIQUID_CARVERS
 * value: lakes and the mineshaft's own writes do not change it.  Pass it
 * (ms116_heightmap() of the status-7 chunk); if NULL it is computed from get()
 * before the first write (exact only when the chunk's lakes did not change any
 * column top).
 *
 * The world given must be the chunk as population reaches step 3: terrain at
 * LIQUID_CARVERS plus the chunk's own lakes (step 1).  Dungeons / fossils of
 * step 3 come after the mineshafts.
 *
 * Simplification (as asked): every piece is generated as if this chunk were the
 * first populated one — the real game keeps per-piece state across chunks
 * (corridor hasSpawner, pieces removed after a failed liquid check).
 *
 * Block codes are the T116_* ones: cave air T116_CAVE_AIR, oak / dark oak planks
 * T116_PLANKS, fences T116_FENCE, rails T116_RAIL, cobwebs T116_COBWEB, wall
 * torches T116_TORCH, cave spider spawners T116_SPAWNER, room floor T116_DIRT.
 * Chest minecarts are entities (only their random draws are reproduced).
 *
 * Thread-safe: no global mutable state.  The float ellipsoid needs no FMA
 * contraction (compile with -ffp-contract=off; the function also asks GCC).
 */
#ifndef MINESHAFT116_H
#define MINESHAFT116_H
#include "terrain116.h"

typedef int  (*Ms116Get)(void *ctx, int x, int y, int z);          /* T116_* code; outside the world -> T116_AIR */
typedef void (*Ms116Set)(void *ctx, int x, int y, int z, int b);

typedef struct { int x0, y0, z0, x1, y1, z1; } Ms116Box;            /* inclusive, like BlockBox */

enum { MS116_ROOM, MS116_CORRIDOR, MS116_CROSSING, MS116_STAIRS };
enum { MS116_N, MS116_S, MS116_W, MS116_E, MS116_NONE };

typedef struct {
    Ms116Box bb;
    signed char type, facing, depth;
    char rails, webs, twofloors;     /* corridor: hasRails / hasCobwebs; crossing: two floors */
    char dir;                        /* crossing direction */
    int length;                      /* corridor: number of 5-block sections */
} Ms116Piece;

typedef struct {
    int cx, cz, mesa;
    Ms116Box bb;                     /* union of the pieces */
    int n, cap;
    Ms116Piece *p;                   /* p[0] is the room */
    int nent;
    Ms116Box ent[64];                /* room entrances */
} Ms116Start;

/* is there a mineshaft start in chunk (cx, cz)?  0 / 1 (normal) / 2 (mesa) */
int  ms116_has_start(const T116 *t, int cx, int cz);
/* build the start of chunk (cx, cz): returns 0 if none.  Free with ms116_start_free. */
int  ms116_start(const T116 *t, int cx, int cz, Ms116Start *s);
void ms116_start_free(Ms116Start *s);

/* place, as chunk (cx, cz)'s population does, every mineshaft piece intersecting
 * the chunk: returns the number of pieces generated (failed liquid checks not
 * counted), -1 on allocation failure. */
int ms116_populate(const T116 *t, int cx, int cz, const int16_t *ocean_floor_wg /* [z*16+x] or NULL */,
                   void *ctx, Ms116Get get, Ms116Set set);

/* OCEAN_FLOOR_WG of a chunk (use the LIQUID_CARVERS chunk, before lakes) */
void ms116_heightmap(const T116Chunk *c, int16_t out[256]);

/* convenience: the same on a T116Chunk c (chunk (c->cx, c->cz), already with its
 * lakes if any); base = the same chunk at LIQUID_CARVERS for the heightmap (or NULL) */
int ms116_populate_chunk(const T116 *t, T116Chunk *c, const T116Chunk *base);

/* does any mineshaft piece box intersect the given block box (inclusive)?
 * Starts are searched within 8 chunks (+ the box extent). */
int ms116_any_piece_in(const T116 *t, int x1, int y1, int z1, int x2, int y2, int z2);

#endif
