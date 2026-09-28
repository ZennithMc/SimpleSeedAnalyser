#include "BoundingBox.h"

stronghold_generator::BoundingBox::BoundingBox(int minX, int minY, int minZ, int maxX, int maxY, int maxZ)
    : minX(minX), minY(minY), minZ(minZ), maxX(maxX), maxY(maxY), maxZ(maxZ) {

}

bool stronghold_generator::BoundingBox::intersects(BoundingBox &boundingBox) {
    return this->maxX >= boundingBox.minX && this->minX <= boundingBox.maxX && this->maxZ >= boundingBox.minZ && this->minZ <= boundingBox.maxZ && this->maxY >= boundingBox.minY && this->minY <= boundingBox.maxY;
}

bool stronghold_generator::BoundingBox::contains(BoundingBox &boundingBox) {
    return this->maxX >= boundingBox.maxX && this->minX <= boundingBox.minX && this->maxZ >= boundingBox.maxZ && this->minZ <= boundingBox.minZ && this->maxY >= boundingBox.maxY && this->minY <= boundingBox.minY;
}

stronghold_generator::BoundingBox stronghold_generator::BoundingBox::orientBox(int x, int y, int z, int offsetWidth, int offsetHeight, int offsetDepth, int width, int height, int depth, Direction direction) {
    switch (direction) {
        case NORTH: {
            return BoundingBox(x + offsetWidth, y + offsetHeight, z - depth + 1 + offsetDepth, x + width - 1 + offsetWidth, y + height - 1 + offsetHeight, z + offsetDepth);
        }
        case SOUTH: {
            return BoundingBox(x + offsetWidth, y + offsetHeight, z + offsetDepth, x + width - 1 + offsetWidth, y + height - 1 + offsetHeight, z + depth - 1 + offsetDepth);
        }
        case WEST: {
            return BoundingBox(x - depth + 1 + offsetDepth, y + offsetHeight, z + offsetWidth, x + offsetDepth, y + height - 1 + offsetHeight, z + width - 1 + offsetWidth);
        }
        case EAST: {
            return BoundingBox(x + offsetDepth, y + offsetHeight, z + offsetWidth, x + depth - 1 + offsetDepth, y + height - 1 + offsetHeight, z + width - 1 + offsetWidth);
        }
    }
    return BoundingBox(0, 0, 0, 0, 0, 0);
}