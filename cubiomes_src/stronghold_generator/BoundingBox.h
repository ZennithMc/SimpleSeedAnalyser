#pragma once

#include "Direction.h"

#include <cstdint>

namespace stronghold_generator {
    struct BoundingBox {
        int16_t minX;
        int16_t minY;
        int16_t minZ;
        int16_t maxX;
        int16_t maxY;
        int16_t maxZ;

        BoundingBox(int minX, int minY, int minZ, int maxX, int maxY, int maxZ);

        bool intersects(BoundingBox &boundingBox);
        bool contains(BoundingBox &boundingBox);

        static BoundingBox orientBox(int x, int y, int z, int offsetWidth, int offsetHeight, int offsetDepth, int width, int height, int depth, Direction direction);
    };
}