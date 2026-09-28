#include "StrongholdGenerator.h"

#include <cstdio>
#include <cmath>
#include <cstdint>
#include <algorithm>
#include <bit>

// #ifdef __AVX2__
//     #include <immintrin.h>
// #endif

void stronghold_generator::StrongholdGenerator::getFirstPosOrigin(long long worldSeed, int &o_chunkX, int &o_chunkZ) {
    Random random(worldSeed);
    double angle = random.nextDouble() * 3.141592653589793 * 2.0;
    double distance = (double)(4 * 32) + (random.nextDouble() - 0.5) * ((double)32 * 2.5);
    o_chunkX = (int)round(cos(angle) * distance);
    o_chunkZ = (int)round(sin(angle) * distance);
}

constexpr uint32_t modinv32(uint32_t value) {
    uint32_t x = ((((value << 1) ^ value) & 4) << 1) ^ value;
    x += x - value * x * x;
    x += x - value * x * x;
    x += x - value * x * x;
    return x;
}

// int ctz(uint32_t val) {
// #ifdef _MSC_VER
//     unsigned long ret;
//     _BitScanForward(&ret, val);
//     return ret;
// #else
//     return __builtin_ctz(val);
// #endif
// }

// template<size_t N, typename Fn>
// #if defined(__cpp_concepts)
//     requires (N >= 1) && requires( Fn fn ) { { fn.template operator ()<(size_t)N - 1>() } -> std::convertible_to<bool>; }
// #endif
// inline bool unroll( Fn fn )
// {
//     auto unroll_n = [&]<size_t ... Indices>( std::index_sequence<Indices ...> ) -> bool
//     {
//         return (fn.template operator ()<Indices>() && ...);
//     };
//     return unroll_n( std::make_index_sequence<N>() );
// }

#define DO_PRAGMA(x) _Pragma(#x)

#if __clang__
#define UNROLL(N) DO_PRAGMA(unroll N)
#elif __GNUC__
#define UNROLL(N) DO_PRAGMA(GCC unroll N)
#else
#define UNROLL(N)
#endif

int findBiomeAllValidReverseIndex(uint64_t seed) {
    stronghold_generator::LCGRandom<0xDFE05BCB1365LLU, 0x615C0E462AA9LLU> random(0); // skip -1
    random.seed = seed * 0xB3174C6F902DLLU + 0x5D05BEA7CD7BLLU; // skip 3249

#if __clang__
    constexpr int unroll = 2;

    uint64_t states[unroll];
    for (int i = 0; i < unroll; i++) {
        random.next(32);
        states[i] = random.seed;
    }

    UNROLL(1624)
    for (int i = 0; i < 1624; i++) {
        uint32_t bits[unroll];
        for (int j = 0; j < unroll; j++) {
            bits[j] = (states[j] >> 17) & 0x7fffffff;
        }

        UNROLL(2)
        for (int j = 0; j < unroll; j++) {
            int idx = i * unroll + j;
            if (bits[j] % (3249 - idx) == 0) {
                return idx;
            }
        }

        for (int j = 0; j < unroll; j++) {
            states[j] = states[j] * 0xe7a191a625d9 + 0x6d6acc228a56; // skip -2
        }
    }

    return 3248;
#else
    struct Data {
        uint32_t muls[3248];
        int rots[3248];
        uint32_t cmps[3248];
    };

    static constexpr Data data = []{
        Data data;
        for (int i = 0; i < 3248; i++) {
            uint32_t mod = 3249 - i;
            if (mod & (mod - 1)) {
                uint32_t mulinv = mod;
                int rot = 0;
                while (mulinv && mulinv % 2 == 0) {
                    mulinv /= 2;
                    rot += 1;
                }
                data.muls[i] = modinv32(mulinv);
                data.rots[i] = rot;
                data.cmps[i] = UINT32_MAX / mod + 1;
            } else {
                data.muls[i] = 1;
                data.rots[i] = 0;
                data.cmps[i] = UINT32_MAX / mod + 1;
            }
        }
        return data;
    }();

    UNROLL(3248)
    for (int i = 0; i < 3248; i++) {
        uint32_t bits = random.next(31);
        if (std::rotr(bits * data.muls[i], data.rots[i]) < data.cmps[i]) {
            return i;
        }
    }

    return 3248;
#endif
}

void stronghold_generator::StrongholdGenerator::getFirstPosFast(long long worldSeed, bool superflat, int &o_chunkX, int &o_chunkZ) {
    Random random(superflat ? 0 : worldSeed);
    double angle = random.nextDouble() * 3.141592653589793 * 2.0;
    double distance = (double)(4 * 32) + (random.nextDouble() - 0.5) * ((double)32 * 2.5);
    int chunkX = (int)round(cos(angle) * distance);
    int chunkZ = (int)round(sin(angle) * distance);
    random.setSeed(random.nextLong());
    if (superflat) {
        o_chunkX = ((chunkX << 4) + 8 - 112 + random.nextInt(112 * 2 + 1)) >> 4;
        o_chunkZ = ((chunkZ << 4) + 8 - 112 + random.nextInt(112 * 2 + 1)) >> 4;
        return;
    }
    int biomeCenterX = ((chunkX << 4) + 8) >> 2;
    int biomeCenterZ = ((chunkZ << 4) + 8) >> 2;
    int idx = findBiomeAllValidReverseIndex(random.seed);
    int dz = 28 - idx / 57;
    int dx = 28 - idx % 57;
    int biomeZ = biomeCenterZ + dz;
    int biomeX = biomeCenterX + dx;
    o_chunkX = biomeX >> 2;
    o_chunkZ = biomeZ >> 2;
}

void stronghold_generator::StrongholdGenerator::getFirstPos(long long worldSeed, int &o_chunkX, int &o_chunkZ) {
    Random random(worldSeed);
    double angle = random.nextDouble() * 3.141592653589793 * 2.0;
    double distance = (double)(4 * 32) + (random.nextDouble() - 0.5) * ((double)32 * 2.5);
    int chunkX = (int)round(cos(angle) * distance);
    int chunkZ = (int)round(sin(angle) * distance);
    random.setSeed(random.nextLong());
    int biomeCenterX = ((chunkX << 4) + 8) >> 2;
    int biomeCenterZ = ((chunkZ << 4) + 8) >> 2;
    int biomeRadius = 112 >> 2;
    int m = 0;
    for (int dz = -biomeRadius; dz <= biomeRadius; dz++) {
        int biomeZ = biomeCenterZ + dz;
        for (int dx = -biomeRadius; dx <= biomeRadius; dx++) {
            int biomeX = biomeCenterX + dx;
            if (m == 0 || random.nextInt(m + 1) == 0) {
                chunkX = biomeX >> 2;
                chunkZ = biomeZ >> 2;
            }
            m++;
        }
    }
    o_chunkX = chunkX;
    o_chunkZ = chunkZ;
}

const stronghold_generator::Direction HORIZONTAL[4] = { stronghold_generator::Direction::NORTH, stronghold_generator::Direction::EAST, stronghold_generator::Direction::SOUTH, stronghold_generator::Direction::WEST };

void stronghold_generator::StrongholdGenerator::generate(long long worldSeed, int chunkX, int chunkZ) {
    int attempt = 0;
    Random random(0);
    do {
        this->resetPieces();
        random.setLargeFeatureSeed(worldSeed + (long long)attempt++, chunkX, chunkZ);
        int x = (chunkX << 4) + 2;
        int z = (chunkZ << 4) + 2;
        this->startX = x;
        this->startZ = z;
        Direction direction = HORIZONTAL[random.nextInt(4)];
        BoundingBox boundingBox = Piece::makeBoundingBox(x, 64, z, direction, 5, 11, 5);
        Piece startPiece = Piece(PieceType::STAIRS_DOWN, 0, boundingBox, direction, 1);
        this->pieces[this->piecesSize++] = startPiece;
        this->addChildren(startPiece, random);
        while (this->pendingPiecesSize != 0 && !this->generationStopped) {
            int i = random.nextInt(this->pendingPiecesSize);
            Piece &piece = this->pieces[this->pendingPieces[i]];
            this->pendingPiecesSize--;
            for (int j = i; j < this->pendingPiecesSize; j++) {
                this->pendingPieces[j] = this->pendingPieces[j + 1];
            }
            this->addChildren(piece, random);
        }
    } while (this->portalRoomPiece == nullptr);
}

stronghold_generator::StrongholdGenerator::StrongholdGenerator() {
    this->resetPieces();
}

void stronghold_generator::StrongholdGenerator::resetPieces() {
    for (int i = 0; i < 11; i++) {
        this->piecePlaceCounts[i] = PiecePlaceCount::DEFAULT[i];
    }
    this->piecePlaceCountsSize = 11;
    this->imposedPiece = PieceType::NONE;
    this->totalWeight = 145;
    this->previousPiece = PieceType::NONE;
    this->piecesSize = 0;
    this->pendingPiecesSize = 0;
    this->portalRoomPiece = nullptr;
    this->generationStopped = false;
}

void stronghold_generator::StrongholdGenerator::updatePieceWeight() {
    for (int i = 0; i < this->piecePlaceCountsSize; i++) {
        PiecePlaceCount piecePlaceCount = this->piecePlaceCounts[i];
        PieceWeight pieceWeight = PieceWeight::PIECE_WEIGHTS[piecePlaceCount.pieceType];
        if (pieceWeight.maxPlaceCount > 0 && piecePlaceCount.placeCount < pieceWeight.maxPlaceCount) {
            return;
        }
    }
    this->generationStopped = true;
}

stronghold_generator::BoundingBox stronghold_generator::StrongholdGenerator::createPieceBoundingBox(PieceType pieceType, int x, int y, int z, Direction direction) {
    switch (pieceType) {
        case PieceType::STRAIGHT: {
            return BoundingBox::orientBox(x, y, z, -1, -1, 0, 5, 5, 7, direction);
        }
        case PieceType::PRISON_HALL: {
            return BoundingBox::orientBox(x, y, z, -1, -1, 0, 9, 5, 11, direction);
        }
        case PieceType::LEFT_TURN: {
            return BoundingBox::orientBox(x, y, z, -1, -1, 0, 5, 5, 5, direction);
        }
        case PieceType::RIGHT_TURN: {
            return BoundingBox::orientBox(x, y, z, -1, -1, 0, 5, 5, 5, direction);
        }
        case PieceType::ROOM_CROSSING: {
            return BoundingBox::orientBox(x, y, z, -4, -1, 0, 11, 7, 11, direction);
        }
        case PieceType::STRAIGHT_STAIRS_DOWN: {
            return BoundingBox::orientBox(x, y, z, -1, -7, 0, 5, 11, 8, direction);
        }
        case PieceType::STAIRS_DOWN: {
            return BoundingBox::orientBox(x, y, z, -1, -7, 0, 5, 11, 5, direction);
        }
        case PieceType::FIVE_CROSSING: {
            return BoundingBox::orientBox(x, y, z, -4, -3, 0, 10, 9, 11, direction);
        }
        case PieceType::CHEST_CORRIDOR: {
            return BoundingBox::orientBox(x, y, z, -1, -1, 0, 5, 5, 7, direction);
        }
        case PieceType::LIBRARY: {
            return BoundingBox::orientBox(x, y, z, -4, -1, 0, 14, 11, 15, direction);
        }
        case PieceType::PORTAL_ROOM: {
            return BoundingBox::orientBox(x, y, z, -4, -1, 0, 11, 8, 16, direction);
        }
        default: {}
    }
    return BoundingBox(0, 0, 0, 0, 0, 0);
}

stronghold_generator::Piece stronghold_generator::StrongholdGenerator::createPiece(PieceType pieceType, Random &random, Direction direction, int depth, BoundingBox boundingBox) {
    switch (pieceType) {
        case PieceType::STRAIGHT: {
            random.nextInt(5);
            int additionalData = 0;
            additionalData |= (random.nextInt(2) == 0) << 0;
            additionalData |= (random.nextInt(2) == 0) << 1;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::PRISON_HALL: {
            random.nextInt(5);
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::LEFT_TURN: {
            random.nextInt(5);
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::RIGHT_TURN: {
            random.nextInt(5);
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::ROOM_CROSSING: {
            random.nextInt(5);
            random.nextInt(5);
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::STRAIGHT_STAIRS_DOWN: {
            random.nextInt(5);
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::STAIRS_DOWN: {
            random.nextInt(5);
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::FIVE_CROSSING: {
            random.nextInt(5);
            int additionalData = 0;
            additionalData |= (random.nextBoolean()) << 0;
            additionalData |= (random.nextBoolean()) << 1;
            additionalData |= (random.nextBoolean()) << 2;
            additionalData |= (random.nextInt(3) > 0) << 3;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::CHEST_CORRIDOR: {
            random.nextInt(5);
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::LIBRARY: {
            random.nextInt(5);
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        case PieceType::PORTAL_ROOM: {
            this->portalRoomPiece = &this->pieces[this->piecesSize];
            this->generationStopped = true;
            int additionalData = 0;
            return Piece(pieceType, depth, boundingBox, direction, additionalData);
        }
        default: {}
    }
    return Piece();
}

void stronghold_generator::StrongholdGenerator::addPiece(Piece piece) {
    // printf("Adding piece at %i %i with rotation %i and depth %i\n", piece.boundingBox.minX, piece.boundingBox.minZ, piece.orientation, piece.depth);
    this->pendingPieces[this->pendingPiecesSize++] = this->piecesSize;
    this->pieces[this->piecesSize++] = piece;
}

bool stronghold_generator::StrongholdGenerator::findAndCreatePieceFactory(PieceType pieceType, Random &random, int x, int y, int z, Direction direction, int depth) {
    BoundingBox boundingBox = this->createPieceBoundingBox(pieceType, x, y, z, direction);
    if(!StrongholdGenerator::isOkBox(boundingBox) || this->findCollisionPiece(boundingBox) != nullptr) {
        if (pieceType == LIBRARY) {
            boundingBox = BoundingBox::orientBox(x, y, z, -4, -1, 0, 14, 6, 15, direction);
            if(!StrongholdGenerator::isOkBox(boundingBox) || this->findCollisionPiece(boundingBox) != nullptr) {
                return false;
            }
        } else {
            return false;
        }
    }

    Piece piece = this->createPiece(pieceType, random, direction, depth, boundingBox);
    this->addPiece(piece);

    return true;
}

void stronghold_generator::StrongholdGenerator::generatePieceFromSmallDoor(Random &random, int x, int y, int z, Direction direction, int depth) {
    if (this->generationStopped) {
        return;
    }

    if (this->imposedPiece != PieceType::NONE) {
        PieceType imposedPiece = this->imposedPiece;
        this->imposedPiece = PieceType::NONE;
        if (this->findAndCreatePieceFactory(imposedPiece, random, x, y, z, direction, depth)) {
            return;
        }
    }

    for (int attempt = 0; attempt < 5; attempt++) {
        int selectedWeight = random.nextInt(this->totalWeight);
        for (int piecePlaceCountIndex = 0; piecePlaceCountIndex < this->piecePlaceCountsSize; piecePlaceCountIndex++) {
            PiecePlaceCount &piecePlaceCount = this->piecePlaceCounts[piecePlaceCountIndex];
            PieceType pieceType = piecePlaceCount.pieceType;
            PieceWeight pieceWeight = PieceWeight::PIECE_WEIGHTS[pieceType];

            if ((selectedWeight -= pieceWeight.weight) >= 0) continue;

            if (!piecePlaceCount.canPlace(depth) || pieceType == this->previousPiece) goto nextAttempt;
            if (!this->findAndCreatePieceFactory(pieceType, random, x, y, z, direction, depth)) continue;
            piecePlaceCount.placeCount++;
            this->previousPiece = pieceType;
            if (!piecePlaceCount.isValid()) {
                this->totalWeight -= pieceWeight.weight;
                this->piecePlaceCountsSize--;
                for (int i = piecePlaceCountIndex; i < this->piecePlaceCountsSize; i++) {
                    this->piecePlaceCounts[i] = this->piecePlaceCounts[i + 1];
                }
                this->updatePieceWeight();
            }
            return;
        }

        nextAttempt:
        (void)0;
    }

    BoundingBox boundingBox = BoundingBox::orientBox(x, y, z, -1, -1, 0, 5, 5, 4, direction);
    Piece *collidingPiece = this->findCollisionPiece(boundingBox);
    if (collidingPiece == nullptr) {
        return;
    }
    if (collidingPiece->boundingBox.minY == boundingBox.minY) {
        for (int i = 2; i >= 1; --i) {
            boundingBox = BoundingBox::orientBox(x, y, z, -1, -1, 0, 5, 5, i, direction);
            if (collidingPiece->boundingBox.intersects(boundingBox)) continue;

            boundingBox = BoundingBox::orientBox(x, y, z, -1, -1, 0, 5, 5, i + 1, direction);
            if (boundingBox.minY > 1) {
                this->addPiece(Piece(PieceType::FILLER_CORRIDOR, depth, boundingBox, direction, 0));
            }
            return;
        }
    }
}

void stronghold_generator::StrongholdGenerator::generateAndAddPiece(Random &random, int x, int y, int z, Direction direction, int depth) {
    if (depth > 50) {
        return;
    }
    if (abs(x - startX) > 112 || abs(z - startZ) > 112) {
        return;
    }
    this->generatePieceFromSmallDoor(random, x, y, z, direction, depth + 1);
}

void stronghold_generator::StrongholdGenerator::generateSmallDoorChildForward(Piece &piece, Random &random, int n, int n2) {
    Direction direction = piece.orientation;
    switch (direction) {
        case Direction::NORTH: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX + n, piece.boundingBox.minY + n2, piece.boundingBox.minZ - 1, direction, piece.depth);
        }
        case Direction::SOUTH: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX + n, piece.boundingBox.minY + n2, piece.boundingBox.maxZ + 1, direction, piece.depth);
        }
        case Direction::WEST: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX - 1, piece.boundingBox.minY + n2, piece.boundingBox.minZ + n, direction, piece.depth);
        }
        case Direction::EAST: {
            return this->generateAndAddPiece(random, piece.boundingBox.maxX + 1, piece.boundingBox.minY + n2, piece.boundingBox.minZ + n, direction, piece.depth);
        }
    }
}

void stronghold_generator::StrongholdGenerator::generateSmallDoorChildLeft(Piece &piece, Random &random, int n, int n2) {
    Direction direction = piece.orientation;
    switch (direction) {
        case Direction::NORTH: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX - 1, piece.boundingBox.minY + n, piece.boundingBox.minZ + n2, Direction::WEST, piece.depth);
        }
        case Direction::SOUTH: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX - 1, piece.boundingBox.minY + n, piece.boundingBox.minZ + n2, Direction::WEST, piece.depth);
        }
        case Direction::WEST: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX + n2, piece.boundingBox.minY + n, piece.boundingBox.minZ - 1, Direction::NORTH, piece.depth);
        }
        case Direction::EAST: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX + n2, piece.boundingBox.minY + n, piece.boundingBox.minZ - 1, Direction::NORTH, piece.depth);
        }
    }
}

void stronghold_generator::StrongholdGenerator::generateSmallDoorChildRight(Piece &piece, Random &random, int n, int n2) {
    Direction direction = piece.orientation;
    switch (direction) {
        case Direction::NORTH: {
            return this->generateAndAddPiece(random, piece.boundingBox.maxX + 1, piece.boundingBox.minY + n, piece.boundingBox.minZ + n2, Direction::EAST, piece.depth);
        }
        case Direction::SOUTH: {
            return this->generateAndAddPiece(random, piece.boundingBox.maxX + 1, piece.boundingBox.minY + n, piece.boundingBox.minZ + n2, Direction::EAST, piece.depth);
        }
        case Direction::WEST: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX + n2, piece.boundingBox.minY + n, piece.boundingBox.maxZ + 1, Direction::SOUTH, piece.depth);
        }
        case Direction::EAST: {
            return this->generateAndAddPiece(random, piece.boundingBox.minX + n2, piece.boundingBox.minY + n, piece.boundingBox.maxZ + 1, Direction::SOUTH, piece.depth);
        }
    }
}

void stronghold_generator::StrongholdGenerator::addChildren(Piece &piece, Random &random) {
    switch (piece.type) {
        case PieceType::STRAIGHT: {
            this->generateSmallDoorChildForward(piece, random, 1, 1);
            if ((piece.additionalData & (1 << 0)) != 0) {
                this->generateSmallDoorChildLeft(piece, random, 1, 2);
            }
            if ((piece.additionalData & (1 << 1)) != 0) {
                this->generateSmallDoorChildRight(piece, random, 1, 2);
            }
        } break;
        case PieceType::PRISON_HALL: {
            this->generateSmallDoorChildForward(piece, random, 1, 1);
        } break;
        case PieceType::LEFT_TURN: {
            Direction direction = piece.orientation;
            if (direction == Direction::NORTH || direction == Direction::EAST) {
                this->generateSmallDoorChildLeft(piece, random, 1, 1);
            } else {
                this->generateSmallDoorChildRight(piece, random, 1, 1);
            }
        } break;
        case PieceType::RIGHT_TURN: {
            Direction direction = piece.orientation;
            if (direction == Direction::NORTH || direction == Direction::EAST) {
                this->generateSmallDoorChildRight(piece, random, 1, 1);
            } else {
                this->generateSmallDoorChildLeft(piece, random, 1, 1);
            }
        } break;
        case PieceType::ROOM_CROSSING: {
            this->generateSmallDoorChildForward(piece, random, 4, 1);
            this->generateSmallDoorChildLeft(piece, random, 1, 4);
            this->generateSmallDoorChildRight(piece, random, 1, 4);
        } break;
        case PieceType::STRAIGHT_STAIRS_DOWN: {
            this->generateSmallDoorChildForward(piece, random, 1, 1);
        } break;
        case PieceType::STAIRS_DOWN: {
            if (piece.additionalData != 0) {
                this->imposedPiece = PieceType::FIVE_CROSSING;
            }
            this->generateSmallDoorChildForward(piece, random, 1, 1);
        } break;
        case PieceType::FIVE_CROSSING: {
            int n = 3;
            int n2 = 5;
            Direction direction = piece.orientation;
            if (direction == Direction::WEST || direction == Direction::NORTH) {
                n = 8 - n;
                n2 = 8 - n2;
            }
            this->generateSmallDoorChildForward(piece, random, 5, 1);
            if ((piece.additionalData & (1 << 0)) != 0) {
                this->generateSmallDoorChildLeft(piece, random, n, 1);
            }
            if ((piece.additionalData & (1 << 1)) != 0) {
                this->generateSmallDoorChildLeft(piece, random, n2, 7);
            }
            if ((piece.additionalData & (1 << 2)) != 0) {
                this->generateSmallDoorChildRight(piece, random, n, 1);
            }
            if ((piece.additionalData & (1 << 3)) != 0) {
                this->generateSmallDoorChildRight(piece, random, n2, 7);
            }
        } break;
        case PieceType::CHEST_CORRIDOR: {
            this->generateSmallDoorChildForward(piece, random, 1, 1);
        } break;
        case PieceType::LIBRARY: {

        } break;
        case PieceType::PORTAL_ROOM: {

        } break;
        case PieceType::FILLER_CORRIDOR: {

        } break;
        default: {}
    }
}

uint64_t total_find_collision_count;
uint64_t total_find_collision_intersects;
uint64_t total_find_collision_null;

stronghold_generator::Piece* stronghold_generator::StrongholdGenerator::findCollisionPiece(BoundingBox &boundingBox) {
    // total_find_collision_count += 1;
    for (int i = 0; i < this->piecesSize; i++) {
        // total_find_collision_intersects += 1;
        if (this->pieces[i].boundingBox.intersects(boundingBox)) {
            return &this->pieces[i];
        }
    }
    // total_find_collision_null += 1;
    return nullptr;
}

bool stronghold_generator::StrongholdGenerator::isOkBox(BoundingBox &boundingBox) {
    return boundingBox.minY > 10;
}