#pragma once

#include "PieceType.h"
#include "BoundingBox.h"
#include "Direction.h"

namespace stronghold_generator {
    struct Piece {
        PieceType type;
        int depth;
        BoundingBox boundingBox;
        Direction orientation;
        int additionalData;

        Piece();
        Piece(PieceType type, int depth, BoundingBox boundingBox, Direction orientation, int additionalData);

        int getWorldX(int offsetWidth, int offsetDepth);
        int getWorldY(int offsetHeight);
        int getWorldZ(int offsetWidth, int offsetDepth);

        static BoundingBox makeBoundingBox(int x, int y, int z, Direction direction, int width, int height, int depth);
    };
}