#pragma once

namespace stronghold_generator {
    struct PieceWeight {
        static const PieceWeight PIECE_WEIGHTS[11];

        int weight;
        int maxPlaceCount;
        int minDepth;
    };
}