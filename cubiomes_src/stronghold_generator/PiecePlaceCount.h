#pragma once

#include "PieceType.h"

namespace stronghold_generator {
    struct PiecePlaceCount {
        static const PiecePlaceCount DEFAULT[11];

        PieceType pieceType;
        int placeCount;

        bool isValid();
        bool canPlace(int depth);
    };
}