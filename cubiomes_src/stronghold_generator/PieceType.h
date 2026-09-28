#pragma once

namespace stronghold_generator {
    enum PieceType {
        STRAIGHT = 0,
        PRISON_HALL = 1,
        LEFT_TURN = 2,
        RIGHT_TURN = 3,
        ROOM_CROSSING = 4,
        STRAIGHT_STAIRS_DOWN = 5,
        STAIRS_DOWN = 6,
        FIVE_CROSSING = 7,
        CHEST_CORRIDOR = 8,
        LIBRARY = 9,
        PORTAL_ROOM = 10,
        FILLER_CORRIDOR = 11,
        NONE,
    };
}