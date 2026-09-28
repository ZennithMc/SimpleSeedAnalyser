#include "Piece.h"

stronghold_generator::Piece::Piece()
    : type(PieceType::NONE), depth(0), boundingBox(0, 0, 0, 0, 0, 0), orientation(Direction::NORTH), additionalData(0) {

}

stronghold_generator::Piece::Piece(PieceType type, int depth, BoundingBox boundingBox, Direction orientation, int additionalData)
    : type(type), depth(depth), boundingBox(boundingBox), orientation(orientation), additionalData(additionalData) {

}

int stronghold_generator::Piece::getWorldX(int offsetWidth, int offsetDepth) {
    switch (this->orientation) {
        case NORTH:
        case SOUTH: {
            return this->boundingBox.minX + offsetWidth;
        }
        case WEST: {
            return this->boundingBox.maxX - offsetDepth;
        }
        case EAST: {
            return this->boundingBox.minX + offsetDepth;
        }
    }
    return 0;
}

int stronghold_generator::Piece::getWorldY(int offsetHeight) {
    return this->boundingBox.minY + offsetHeight;
}

int stronghold_generator::Piece::getWorldZ(int offsetWidth, int offsetDepth) {
    switch (this->orientation) {
        case NORTH: {
            return this->boundingBox.maxZ - offsetDepth;
        }
        case SOUTH: {
            return this->boundingBox.minZ + offsetDepth;
        }
        case WEST:
        case EAST: {
            return this->boundingBox.minZ + offsetWidth;
        }
    }
    return 0;
}

stronghold_generator::BoundingBox stronghold_generator::Piece::makeBoundingBox(int x, int y, int z, Direction direction, int width, int height, int depth) {
    if (direction == Direction::NORTH || direction == Direction::SOUTH) {
        return BoundingBox(x, y, z, x + width - 1, y + height - 1, z + depth - 1);
    } else {
        return BoundingBox(x, y, z, x + depth - 1, y + height - 1, z + width - 1);
    }
}