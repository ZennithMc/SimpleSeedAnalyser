#include "PiecePlaceCount.h"

#include "PieceWeight.h"

const stronghold_generator::PiecePlaceCount stronghold_generator::PiecePlaceCount::DEFAULT[] = {
    { stronghold_generator::PieceType::STRAIGHT            , 0 },
    { stronghold_generator::PieceType::PRISON_HALL         , 0 },
    { stronghold_generator::PieceType::LEFT_TURN           , 0 },
    { stronghold_generator::PieceType::RIGHT_TURN          , 0 },
    { stronghold_generator::PieceType::ROOM_CROSSING       , 0 },
    { stronghold_generator::PieceType::STRAIGHT_STAIRS_DOWN, 0 },
    { stronghold_generator::PieceType::STAIRS_DOWN         , 0 },
    { stronghold_generator::PieceType::FIVE_CROSSING       , 0 },
    { stronghold_generator::PieceType::CHEST_CORRIDOR      , 0 },
    { stronghold_generator::PieceType::LIBRARY             , 0 },
    { stronghold_generator::PieceType::PORTAL_ROOM         , 0 },
};

bool stronghold_generator::PiecePlaceCount::isValid() {
    int maxPlaceCount = PieceWeight::PIECE_WEIGHTS[this->pieceType].maxPlaceCount;
    return maxPlaceCount == 0 || this->placeCount < maxPlaceCount;
}

bool stronghold_generator::PiecePlaceCount::canPlace(int depth) {
    return this->isValid() && depth >= PieceWeight::PIECE_WEIGHTS[this->pieceType].minDepth;
}