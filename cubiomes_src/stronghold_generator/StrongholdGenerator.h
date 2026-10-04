#pragma once

#include "LCGRandom.h"
#include "Direction.h"
#include "BoundingBox.h"
#include "PieceType.h"
#include "PieceWeight.h"
#include "PiecePlaceCount.h"
#include "Piece.h"

namespace stronghold_generator {
    struct StrongholdGenerator {
        // more than MAX_PIECES pieces (never seen: ~680 max in millions): the
        // generation stops instead of writing past the arrays; topY says so.
        static const int MAX_PIECES = 1024;
        static const int TOPY_OVERFLOW = -100000;
        PiecePlaceCount piecePlaceCounts[11];
        int piecePlaceCountsSize;
        PieceType imposedPiece;
        int totalWeight;
        PieceType previousPiece;
        Piece pieces[MAX_PIECES];
        int piecesSize;
        int pendingPieces[MAX_PIECES];
        int pendingPiecesSize;
        int startX;
        int startZ;
        Piece *portalRoomPiece;
        bool generationStopped;
        bool pre118 = false;  // MC 1.13-1.17 : FillerCorridor different
        bool fullBfs = false; // ne pas stopper au portail (pour le Max Y)
        bool overflowed = false;

        // Collision grid (03/10, SSA): XZ cells of 16 blocks around the start, each
        // listing the indices of the pieces that overlap it, in insertion order.
        // findCollisionPiece returns exactly the same piece as the linear scan (the
        // lowest index that intersects); any box outside the grid, or a full cell,
        // switches back to the linear scan for the whole stronghold.
        static const int GRID_N = 24, GRID_CAP = 48;
        int gridOX, gridOZ;
        bool gridOn;
        unsigned char gridCount[GRID_N * GRID_N];
        short gridItems[GRID_N * GRID_N][GRID_CAP];
        void gridReset();
        void gridInsert(int index);
        void pushPiece(const Piece &piece);   // appends to pieces[] (+ grid)

        StrongholdGenerator();

        static void getFirstPosOrigin(long long worldSeed, int &chunkX, int &chunkZ);
        static void getFirstPosFast(long long worldSeed, bool superflat, int &chunkX, int &chunkZ);
        static void getFirstPos(long long worldSeed, int &chunkX, int &chunkZ);
        void generate(long long worldSeed, int chunkX, int chunkZ);
        int topY(long long worldSeed, int chunkX, int chunkZ, int sea, int margin, int minY, int inflate);

        void resetPieces();
        void updatePieceWeight();
        BoundingBox createPieceBoundingBox(PieceType pieceType, int x, int y, int z, Direction direction);
        Piece createPiece(PieceType pieceType, Random &random, Direction direction, int depth, BoundingBox boundingBox);
        void addPiece(Piece piece);
        bool findAndCreatePieceFactory(PieceType pieceType, Random &random, int x, int y, int z, Direction direction, int depth);
        void generatePieceFromSmallDoor(Random &random, int x, int y, int z, Direction direction, int depth);
        void generateAndAddPiece(Random &random, int x, int y, int z, Direction direction, int depth);
        void generateSmallDoorChildForward(Piece &piece, Random &random, int n, int n2);
        void generateSmallDoorChildLeft(Piece &piece, Random &random, int n, int n2);
        void generateSmallDoorChildRight(Piece &piece, Random &random, int n, int n2);
        void addChildren(Piece &piece, Random &random);

        Piece* findCollisionPiece(BoundingBox &boundingBox);

        static bool isOkBox(BoundingBox &boundingBox);
    };
};