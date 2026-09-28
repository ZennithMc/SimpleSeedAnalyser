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
        PiecePlaceCount piecePlaceCounts[11];
        int piecePlaceCountsSize;
        PieceType imposedPiece;
        int totalWeight;
        PieceType previousPiece;
        Piece pieces[1024];
        int piecesSize;
        int pendingPieces[1024];
        int pendingPiecesSize;
        int startX;
        int startZ;
        Piece *portalRoomPiece;
        bool generationStopped;

        StrongholdGenerator();

        static void getFirstPosOrigin(long long worldSeed, int &chunkX, int &chunkZ);
        static void getFirstPosFast(long long worldSeed, bool superflat, int &chunkX, int &chunkZ);
        static void getFirstPos(long long worldSeed, int &chunkX, int &chunkZ);
        void generate(long long worldSeed, int chunkX, int chunkZ);

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