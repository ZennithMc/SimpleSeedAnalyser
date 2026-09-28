#include "gaider10_lib.h"

#include <climits>
#include <cstdlib>
#include <algorithm>

#include "stronghold_generator/StrongholdGenerator.h"

extern "C" EXPORT uint32_t generate_layouts_v(uint64_t, uint64_t, int, int, Layout *, uint32_t);

extern "C" EXPORT uint32_t generate_layouts(uint64_t structure_seed_start, uint64_t structure_seed_end, int superflat, Layout *out, uint32_t out_len) {
    return generate_layouts_v(structure_seed_start, structure_seed_end, superflat, 0, out, out_len);
}

extern "C" EXPORT uint32_t generate_layouts_v(uint64_t structure_seed_start, uint64_t structure_seed_end, int superflat, int pre118, Layout *out, uint32_t out_len) {
    uint32_t count = 0;

    if (count >= out_len) return count;

    stronghold_generator::StrongholdGenerator strongholdGenerator;
    strongholdGenerator.pre118 = (pre118 != 0);
    for (uint64_t structure_seed = structure_seed_start; structure_seed < structure_seed_end; structure_seed++) {
        int startChunkX, startChunkZ;
        stronghold_generator::StrongholdGenerator::getFirstPosFast(structure_seed, superflat, startChunkX, startChunkZ);
        // stronghold_generator::StrongholdGenerator::getFirstPos(structure_seed, startChunkX, startChunkZ);
        // stronghold_generator::StrongholdGenerator::getFirstPosOrigin(structure_seed, startChunkX, startChunkZ);
        strongholdGenerator.generate(structure_seed, startChunkX, startChunkZ);
        stronghold_generator::Piece *portalRoomPiece = strongholdGenerator.portalRoomPiece;
        int minChunkX = portalRoomPiece->getWorldX(4,  9) >> 4;
        int maxChunkX = portalRoomPiece->getWorldX(6, 11) >> 4;
        if (minChunkX > maxChunkX) {
            std::swap(minChunkX, maxChunkX);
        }
        int minChunkZ = portalRoomPiece->getWorldZ(4,  9) >> 4;
        int maxChunkZ = portalRoomPiece->getWorldZ(6, 11) >> 4;
        if (minChunkZ > maxChunkZ) {
            std::swap(minChunkZ, maxChunkZ);
        }
        for (int chunkX = minChunkX; chunkX <= maxChunkX; chunkX++) {
            for (int chunkZ = minChunkZ; chunkZ <= maxChunkZ; chunkZ++) {
                stronghold_generator::BoundingBox chunkBox(chunkX << 4, INT16_MIN, chunkZ << 4, (chunkX << 4) + 15, INT16_MAX, (chunkZ << 4) + 15);

                bool is_first = true;
                for (int i = 0; i < strongholdGenerator.piecesSize - 1; i++) {
                    if (chunkBox.intersects(strongholdGenerator.pieces[i].boundingBox)) {
                        is_first = false;
                        break;
                    }
                }
                if (!is_first) continue;

                out[count++] = Layout{structure_seed, (int16_t)startChunkX, (int16_t)chunkX, (int16_t)startChunkZ, (int16_t)chunkZ};
                if (count >= out_len) return count;
            }
        }
    }

    return count;
}

extern "C" EXPORT void ssa_test_portal(uint64_t seed, int cx, int cz, int pre118, int *out) {
    stronghold_generator::StrongholdGenerator g;
    g.pre118 = (pre118 != 0);
    g.generate((long long)seed, cx, cz);
    stronghold_generator::Piece *p = g.portalRoomPiece;
    out[0] = p->boundingBox.minX;
    out[1] = p->boundingBox.minZ;
    out[2] = p->getWorldX(5, 10);
    out[3] = p->getWorldZ(5, 10);
    out[4] = g.piecesSize;
}

// FAST end-portal frame X,Z (no Y) for the corrupted scan. The X,Z are exact
// (only the moveBelowSeaLevel Y differs from MC, which we don't use here — the
// Python BFS / checker handle Y). out[27] = {valid, centerX, centerZ,
//   x0,z0, x1,z1, ... x11,z11 (the 12 frame columns)}.
extern "C" EXPORT int spg_frames_xz(uint64_t seed, int cx, int cz, int pre118, int *out) {
    static const int FL[12][2] = {{4,8},{5,8},{6,8},{4,12},{5,12},{6,12},
                                  {3,9},{3,10},{3,11},{7,9},{7,10},{7,11}};
    stronghold_generator::StrongholdGenerator g;
    g.pre118 = (pre118 != 0);
    g.generate((long long)seed, cx, cz);
    stronghold_generator::Piece *p = g.portalRoomPiece;
    if (!p) { out[0] = 0; return 0; }
    out[0] = 1;
    out[1] = p->getWorldX(5, 10);
    out[2] = p->getWorldZ(5, 10);
    for (int k = 0; k < 12; k++) {
        out[3 + 2*k] = p->getWorldX(FL[k][0], FL[k][1]);
        out[4 + 2*k] = p->getWorldZ(FL[k][0], FL[k][1]);
    }
    return 1;
}

// getFirstPosFast — the 48-bit (biome-FREE) 1st-stronghold start chunk, i.e. the
// ring estimate the eye finder already uses. out[2] = {chunkX, chunkZ}. The ring
// RNG is version-independent; only the biome snap (which this skips) differs.
extern "C" EXPORT void spg_first_pos(uint64_t seed, int superflat, int *out) {
    int cx = 0, cz = 0;
    stronghold_generator::StrongholdGenerator::getFirstPosFast(
        (long long)seed, superflat != 0, cx, cz);
    out[0] = cx; out[1] = cz;
}


extern "C" EXPORT uint32_t max_y_batch(int64_t seed_start, int32_t n,
        const int32_t *cxs, const int32_t *czs, int pre118,
        int sea, int margin, int minY, int inflate, int threshold,
        int64_t *out_seeds, int32_t *out_y) {
    stronghold_generator::StrongholdGenerator g;
    g.pre118 = (pre118 != 0);
    g.fullBfs = true;
    uint32_t cnt = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seed_start + (int64_t)i;
        int top = g.topY((long long)seed, cxs[i], czs[i], sea, margin, minY, inflate);
        if (top >= threshold) { out_seeds[cnt] = seed; out_y[cnt] = top; cnt++; }
    }
    return cnt;
}

// Seed-ARRAY variant of max_y_batch (seedlist mode): topY per (seed, chunk),
// keep survivors >= threshold. cxs/czs = cubiomes-snapped start chunks (from
// seedlist_first_stronghold). out_seeds/out_y receive the survivors.
extern "C" EXPORT uint32_t max_y_batch_arr(const int64_t *seeds, int32_t n,
        const int32_t *cxs, const int32_t *czs, int pre118,
        int sea, int margin, int minY, int inflate, int threshold,
        int64_t *out_seeds, int32_t *out_y) {
    stronghold_generator::StrongholdGenerator g;
    g.pre118 = (pre118 != 0);
    g.fullBfs = true;
    uint32_t cnt = 0;
    for (int32_t i = 0; i < n; i++) {
        int64_t seed = seeds[i];
        int top = g.topY((long long)seed, cxs[i], czs[i], sea, margin, minY, inflate);
        if (top >= threshold) { out_seeds[cnt] = seed; out_y[cnt] = top; cnt++; }
    }
    return cnt;
}

extern "C" EXPORT int gaider_maxy_inflate() { return 1; }  // marqueur: max_y_batch a le param inflate
