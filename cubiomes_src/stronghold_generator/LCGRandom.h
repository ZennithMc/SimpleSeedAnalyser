#pragma once

namespace stronghold_generator {
    template<unsigned long long M, unsigned long long A>
    struct LCGRandom {
        static const unsigned long long MULTIPLIER = M;
        static const unsigned long long ADDEND = A;
        static const unsigned long long MASK = (1LLU << 48) - 1LLU;

        unsigned long long seed;

        LCGRandom(unsigned long long seed)
            : seed((seed ^ 0x5DEECE66DLLU) & MASK) {

        }

        void setSeed(unsigned long long seed) {
            this->seed = (seed ^ 0x5DEECE66DLLU) & MASK;
        }

        int next(int bits) {
            this->seed = (this->seed * MULTIPLIER + ADDEND) & MASK;
            return (int)(this->seed >> (48 - bits));
        }

        int nextInt() {
            return this->next(32);
        }

        int nextInt(int bound) {
            int r = this->next(31);
            int m = bound - 1;
            if ((bound & m) == 0)  // i.e., bound is a power of 2
                r = (int)((bound * (long long)r) >> 31);
            else { // reject over-represented candidates
                for (int u = r;
                        u - (r = u % bound) + m < 0;
                        u = this->next(31))
                    ;
            }
            return r;
        }

        long long nextLong() {
            return ((long long)this->next(32) << 32) + (long long)this->next(32);
        }

        bool nextBoolean() {
            return this->next(1) != 0;
        }

        float nextFloat() {
            return this->next(24) / ((float)(1 << 24));
        }

        double nextDouble() {
            return (((long long)(next(26)) << 27) + (long long)next(27)) * 0x1.0p-53;
        }

        long long setDecorationSeed(long long worldSeed, int x, int z) {
            this->setSeed(worldSeed);
            long long l2 = this->nextLong() | 1LL;
            long long l3 = this->nextLong() | 1LL;
            long long l4 = (long long)x * l2 + (long long)z * l3 ^ worldSeed;
            this->setSeed(l4);
            return l4;
        }

        void setFeatureSeed(long long decorationSeed, int index, int step) {
            long long l2 = decorationSeed + (long long)index + (long long)(10000 * step);
            this->setSeed(l2);
        }

        void setLargeFeatureSeed(long long worldSeed, int chunkX, int chunkZ) {
            this->setSeed(worldSeed);
            long long l2 = this->nextLong();
            long long l3 = this->nextLong();
            long long l4 = (long long)chunkX * l2 ^ (long long)chunkZ * l3 ^ worldSeed;
            this->setSeed(l4);
        }

        void setLargeFeatureWithSalt(long long worldSeed, int regionX, int regionZ, int salt) {
            long long l2 = (long long)regionX * 341873128712LL + (long long)regionZ * 132897987541LL + worldSeed + (long long)salt;
            this->setSeed(l2);
        }
    };

    using Random = LCGRandom<0x5DEECE66DLLU, 0xBLLU>;
}