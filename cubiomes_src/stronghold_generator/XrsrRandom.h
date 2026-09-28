#pragma once

namespace stronghold_generator {
    struct XrsrRandom {
        static unsigned long long mixStafford13(unsigned long long l);

        unsigned long long seed_lo;
        unsigned long long seed_hi;

        XrsrRandom(unsigned long long seed);
        XrsrRandom(unsigned long long seed_lo, unsigned long long seed_hi);
        void setSeed(unsigned long long seed);

        void skipPortalRoom();

        int next(int bits);
        int nextInt();
        int nextInt(int bound);
        long long nextLong();
        bool nextBoolean();
        float nextFloat();
        double nextDouble();

        long long setDecorationSeed(long long worldSeed, int x, int z);
        void setFeatureSeed(long long decorationSeed, int index, int step);
        void setLargeFeatureSeed(long long worldSeed, int chunkX, int chunkZ);
        void setLargeFeatureWithSalt(long long worldSeed, int regionX, int regionZ, int salt);
    };
}