#include "XrsrRandom.h"

unsigned long long stronghold_generator::XrsrRandom::mixStafford13(unsigned long long l) {
    l = (l ^ l >> 30) * -4658895280553007687LLU;
    l = (l ^ l >> 27) * -7723592293110705685LLU;
    return l ^ l >> 31;
}

stronghold_generator::XrsrRandom::XrsrRandom(unsigned long long seed)
    : seed_lo(mixStafford13(seed ^= 0x6A09E667F3BCC909LLU)), seed_hi(mixStafford13(seed + -7046029254386353131LLU)) {

}

stronghold_generator::XrsrRandom::XrsrRandom(unsigned long long seed_lo, unsigned long long seed_hi)
    : seed_lo(seed_lo), seed_hi(seed_hi) {
    if ((seed_lo | seed_hi) == 0) {
        this->seed_lo = -7046029254386353131LLU;
        this->seed_hi = 7640891576956012809LLU;
    }
}

void stronghold_generator::XrsrRandom::setSeed(unsigned long long seed) {
    this->seed_lo = mixStafford13(seed ^= 0x6A09E667F3BCC909LLU);
    this->seed_hi = mixStafford13(seed + -7046029254386353131LLU);
}

#define XOR(reg, idx, him, lom) \
	if (reg & (unsigned long long) 1 << idx) { \
		hi ^= him; \
		lo ^= lom; \
	}

void stronghold_generator::XrsrRandom::skipPortalRoom() {
    unsigned long long lo = 0;
    unsigned long long hi = 0;
    XOR(this->seed_lo, 0, 0xbea1cb0e235828b3ULL, 0x9729b1f34956cf87ULL)
    XOR(this->seed_lo, 1, 0x30d9b3a637b8cc30ULL, 0x69438152e6e4137bULL)
    XOR(this->seed_lo, 2, 0xef9ba2a91df9826aULL, 0x54d7aa11544d7f0dULL)
    XOR(this->seed_lo, 3, 0x6b77a2a23f77b567ULL, 0x184dd2690f183868ULL)
    XOR(this->seed_lo, 4, 0x8ee1a0478680083aULL, 0x66cb946dcd298822ULL)
    XOR(this->seed_lo, 5, 0x7396c0e3bb0fca9aULL, 0x715a6f94782b3af2ULL)
    XOR(this->seed_lo, 6, 0x9386458f6709c14aULL, 0xa57b30c71d13dd35ULL)
    XOR(this->seed_lo, 7, 0x77b6ca6dbf1381b0ULL, 0x860757166f353f18ULL)
    XOR(this->seed_lo, 8, 0xe98f1e3a0e68ee6bULL, 0x2eb84f434773c0f0ULL)
    XOR(this->seed_lo, 9, 0x48a8feb481412058ULL, 0x6ec4a811d3f5e13aULL)
    XOR(this->seed_lo, 10, 0x2acb074fd1528435ULL, 0x310cb9f1bb763692ULL)
    XOR(this->seed_lo, 11, 0x07cfecc893741e1eULL, 0x54445aa20b269e02ULL)
    XOR(this->seed_lo, 12, 0x175f9549f77a39adULL, 0xe9066c93af9dfdb0ULL)
    XOR(this->seed_lo, 13, 0x4426c48954682e38ULL, 0x62c2b5e8e0d69c9bULL)
    XOR(this->seed_lo, 14, 0x06eeb208c90ce28cULL, 0x5e5ef7e00ed35ea9ULL)
    XOR(this->seed_lo, 15, 0xb20ad12841018041ULL, 0xb1b3fcfb522ae01bULL)
    XOR(this->seed_lo, 16, 0xf94466f60cc9cfd9ULL, 0xfc7cf254b6da0494ULL)
    XOR(this->seed_lo, 17, 0x7420dd36650b4e03ULL, 0xe6ee807dd4f693c4ULL)
    XOR(this->seed_lo, 18, 0xa4d5e84842496c56ULL, 0x7ae8e208f7190436ULL)
    XOR(this->seed_lo, 19, 0x0a1651d2513089caULL, 0xd1ea97de107b32c9ULL)
    XOR(this->seed_lo, 20, 0xfa7bfb681850a9a3ULL, 0x5f5291a6283d5e3bULL)
    XOR(this->seed_lo, 21, 0xef063377f32c1b27ULL, 0xe075e04ebb0cd1a4ULL)
    XOR(this->seed_lo, 22, 0x8ee415201f9ac0d7ULL, 0x4a94290c864c26abULL)
    XOR(this->seed_lo, 23, 0x59d29bb6ecd74a35ULL, 0x5e5ddf9a3ab76eccULL)
    XOR(this->seed_lo, 24, 0x70d37d8062ede900ULL, 0x80b8f5bd4f4296e2ULL)
    XOR(this->seed_lo, 25, 0xc325d770d103a272ULL, 0x14babf1d5d7b1bf1ULL)
    XOR(this->seed_lo, 26, 0x1386ef197be907e6ULL, 0xfe7068c56f7362b3ULL)
    XOR(this->seed_lo, 27, 0x378500ecc90a9cf1ULL, 0xd22c04459a7949c0ULL)
    XOR(this->seed_lo, 28, 0xfe49609deaa81ae4ULL, 0x45f33f678cfa46dfULL)
    XOR(this->seed_lo, 29, 0xc8df216f835c4cdcULL, 0x0cbf3a8b7a964ff5ULL)
    XOR(this->seed_lo, 30, 0xadf96f98af5a1ec9ULL, 0xb0c0a793d68846d9ULL)
    XOR(this->seed_lo, 31, 0x814f25287c709c9bULL, 0x148a61faa04a5b72ULL)
    XOR(this->seed_lo, 32, 0xe0165b6fcb8e09f0ULL, 0xa500b82a5dd0442fULL)
    XOR(this->seed_lo, 33, 0xa559a881ba91e661ULL, 0x4ab3bd9ea43a43fcULL)
    XOR(this->seed_lo, 34, 0x828686ae4bf3b1e7ULL, 0x10a26e2069b47539ULL)
    XOR(this->seed_lo, 35, 0x354a4970c3798857ULL, 0x6617a2470d9126f2ULL)
    XOR(this->seed_lo, 36, 0x627399b8aeff223bULL, 0xc8cb1f7ff71d7799ULL)
    XOR(this->seed_lo, 37, 0xf5dc48949583aab8ULL, 0xf5da697d4650b5b4ULL)
    XOR(this->seed_lo, 38, 0x20b68ce2799464a4ULL, 0xaed50dfa8e1f2c0aULL)
    XOR(this->seed_lo, 39, 0x6ce16fa557508398ULL, 0x00621d3359224c5dULL)
    XOR(this->seed_lo, 40, 0xf5437e0d460fb080ULL, 0xdd0d3cab187ad8cdULL)
    XOR(this->seed_lo, 41, 0x5a6784625179eb67ULL, 0x0a7dff3ceca8340aULL)
    XOR(this->seed_lo, 42, 0x5bada798d37bf1daULL, 0x66c0ea498ae30744ULL)
    XOR(this->seed_lo, 43, 0xf49f3202af8b828dULL, 0x9649d0792a7c0f4dULL)
    XOR(this->seed_lo, 44, 0x06a6f88edfd5f696ULL, 0xdc21a06b0df2c4d7ULL)
    XOR(this->seed_lo, 45, 0xa958de14f8755f8eULL, 0x5c36949458eb5ff4ULL)
    XOR(this->seed_lo, 46, 0xf41b985d37852acaULL, 0x67840ae2d8980411ULL)
    XOR(this->seed_lo, 47, 0x0889eabc540cb5fcULL, 0x1002322c32d607c3ULL)
    XOR(this->seed_lo, 48, 0x2df8ffea481d069aULL, 0xd08621bb396c6752ULL)
    XOR(this->seed_lo, 49, 0x323e4d1e502fcc2eULL, 0xd677a3a5fbcf0701ULL)
    XOR(this->seed_lo, 50, 0x2a1b5a014f1e28d6ULL, 0x8fbc61e11fb583e8ULL)
    XOR(this->seed_lo, 51, 0x79920c49fc2295feULL, 0x1eb45493f04b9382ULL)
    XOR(this->seed_lo, 52, 0xa6f2980b77654919ULL, 0xd5706cc1e0b7274eULL)
    XOR(this->seed_lo, 53, 0x49242973f0d38bc5ULL, 0xbaae62ca0da195eeULL)
    XOR(this->seed_lo, 54, 0x5bb41874775c1cf5ULL, 0x9a1086f162376218ULL)
    XOR(this->seed_lo, 55, 0xa1c27475a00c0d56ULL, 0x5182ac806d5cecefULL)
    XOR(this->seed_lo, 56, 0xfa8abd84ca3bf283ULL, 0xfb2ef04531097121ULL)
    XOR(this->seed_lo, 57, 0xca58f43d9cb60c51ULL, 0xac19179c3d689655ULL)
    XOR(this->seed_lo, 58, 0x4a258cc0cdea0d50ULL, 0xed43af5dd7af7accULL)
    XOR(this->seed_lo, 59, 0xcc1823d552f2c042ULL, 0x468128efb2d5f96cULL)
    XOR(this->seed_lo, 60, 0xedc3c15e60d7a055ULL, 0xb25a4a5386305762ULL)
    XOR(this->seed_lo, 61, 0x9b3edfebd022f75fULL, 0x29041dcb27c38d24ULL)
    XOR(this->seed_lo, 62, 0xb4a161f08d70845aULL, 0xc64435382757e8c5ULL)
    XOR(this->seed_lo, 63, 0x1476f5e55753c1e6ULL, 0x66d8209886988ee9ULL)
    XOR(this->seed_hi, 0, 0x32b37ba8ebcebd21ULL, 0x19c7c3b6a2248bf0ULL)
    XOR(this->seed_hi, 1, 0xac20e08487f30521ULL, 0x04585f12f71a7a7eULL)
    XOR(this->seed_hi, 2, 0x3a04800b2f57d6c2ULL, 0x4361d754ffc3ef3aULL)
    XOR(this->seed_hi, 3, 0x9116ff8703a2fde4ULL, 0x94781ed65ce4f05cULL)
    XOR(this->seed_hi, 4, 0xc8bf201295aa0187ULL, 0x2dada86c4c4116a0ULL)
    XOR(this->seed_hi, 5, 0x169d177d72e1cf6eULL, 0x6190775f0a84b841ULL)
    XOR(this->seed_hi, 6, 0x6dc7ddf8aef47810ULL, 0x5bfa7ddac8c92335ULL)
    XOR(this->seed_hi, 7, 0xb2892a43430ef64eULL, 0xe0879f073b8390aaULL)
    XOR(this->seed_hi, 8, 0x935baac54121cfc4ULL, 0x9f0c3ea0adb5a286ULL)
    XOR(this->seed_hi, 9, 0x0ea8518bf879a5efULL, 0xf664d22a0895a881ULL)
    XOR(this->seed_hi, 10, 0x8ba56f98f2cc4f7aULL, 0xbf4ed50a596ac088ULL)
    XOR(this->seed_hi, 11, 0xeef21af06e0a0185ULL, 0xb003a9c42a64947fULL)
    XOR(this->seed_hi, 12, 0x9fb641777d5b0d1eULL, 0x63c0cd54965137beULL)
    XOR(this->seed_hi, 13, 0x0a06c702e598e322ULL, 0xfbeaf3c81b304e89ULL)
    XOR(this->seed_hi, 14, 0x1a5ba9c2663dd33fULL, 0x37d7f24be68d314fULL)
    XOR(this->seed_hi, 15, 0xbb27059694afd248ULL, 0x8d1ca37e6d9802f0ULL)
    XOR(this->seed_hi, 16, 0xfa53f67a95deccedULL, 0x84e7a1db22563dc3ULL)
    XOR(this->seed_hi, 17, 0xf6458473367f4bdaULL, 0xbb13ff967e38bc8cULL)
    XOR(this->seed_hi, 18, 0x6603c03ffc16d685ULL, 0x4f459c09fe3c94c6ULL)
    XOR(this->seed_hi, 19, 0x085404637907b059ULL, 0xbba841c7d6560f22ULL)
    XOR(this->seed_hi, 20, 0xbf5e11dcbb585d38ULL, 0xaa8bd2b5b259a49bULL)
    XOR(this->seed_hi, 21, 0x8be8ba0509a9e3b1ULL, 0xf4077d9579ed71c4ULL)
    XOR(this->seed_hi, 22, 0x6f1ee9bba817ede4ULL, 0x5a56ce904aadad43ULL)
    XOR(this->seed_hi, 23, 0x64bca8c9658ea667ULL, 0x85bd0a5b2b1f613eULL)
    XOR(this->seed_hi, 24, 0x9af56039ed5b676bULL, 0x7e8ced804a7bf3c1ULL)
    XOR(this->seed_hi, 25, 0x605b50115c8471b7ULL, 0xa2ec7b2e7c4cbcc9ULL)
    XOR(this->seed_hi, 26, 0x368afd852e5264eaULL, 0xec3d9bb94636cd36ULL)
    XOR(this->seed_hi, 27, 0xe492beffe8642a2bULL, 0x76982087ceeeab85ULL)
    XOR(this->seed_hi, 28, 0xb5395b14c2c37815ULL, 0x4801e76ae6fd6cbbULL)
    XOR(this->seed_hi, 29, 0xd9333173d2a5871eULL, 0x8d9d02574584a4bbULL)
    XOR(this->seed_hi, 30, 0x4b407a8076b89d2aULL, 0x4661b6902fc7d7beULL)
    XOR(this->seed_hi, 31, 0x0e50bff9ddccb272ULL, 0x570906b495b01afbULL)
    XOR(this->seed_hi, 32, 0xa05ac64529d282cfULL, 0xb5f8c537fd811c76ULL)
    XOR(this->seed_hi, 33, 0x4796788837961b7eULL, 0x8ceaa8fd64633a67ULL)
    XOR(this->seed_hi, 34, 0x826090d1fd6087b9ULL, 0x034ce4ff7e425e42ULL)
    XOR(this->seed_hi, 35, 0xce777c9e109b7000ULL, 0x18200d9679cc6824ULL)
    XOR(this->seed_hi, 36, 0x7f997981adf1dbf1ULL, 0xe7e04d380470b92cULL)
    XOR(this->seed_hi, 37, 0xfa5f51fac197f6abULL, 0x03f57d6bfc05bba6ULL)
    XOR(this->seed_hi, 38, 0x813e4a988e6ca307ULL, 0xc22968d5e4ce50c7ULL)
    XOR(this->seed_hi, 39, 0xa39f5ef8fd150cddULL, 0xe1c2bc69fe501b1eULL)
    XOR(this->seed_hi, 40, 0x8cb69e62422cb70eULL, 0xb4c2fbe82e04a84eULL)
    XOR(this->seed_hi, 41, 0x694ac8fda0855c93ULL, 0x87c0cdc2253a7269ULL)
    XOR(this->seed_hi, 42, 0xabaff55f08adaf63ULL, 0x066b779d29655731ULL)
    XOR(this->seed_hi, 43, 0x5fea359b6c9d1814ULL, 0x97bd79af0dfc9c0aULL)
    XOR(this->seed_hi, 44, 0x17e0a1707ea1201bULL, 0xf8a33459217404a2ULL)
    XOR(this->seed_hi, 45, 0x00122ea1784a4d19ULL, 0x00dd88bb0742b4e9ULL)
    XOR(this->seed_hi, 46, 0xa81623c9278d8a0dULL, 0xfc8078cedc62f651ULL)
    XOR(this->seed_hi, 47, 0xb5a03428ac00fd10ULL, 0x15cb73b6cbbff9d3ULL)
    XOR(this->seed_hi, 48, 0x308c609078741ef2ULL, 0x64ecd8db5c9ff721ULL)
    XOR(this->seed_hi, 49, 0xb3afddc877a91684ULL, 0xe8dd2d0bf421b74fULL)
    XOR(this->seed_hi, 50, 0xc4508328c345c028ULL, 0x566f3dbbc74afc1aULL)
    XOR(this->seed_hi, 51, 0x7b9d7f4dad12a68aULL, 0xf349042186423b7cULL)
    XOR(this->seed_hi, 52, 0x43f9aade34df2e55ULL, 0x6841f59bfec9c94bULL)
    XOR(this->seed_hi, 53, 0xc532c3fa0aadfa0bULL, 0xaffec201fcb72a07ULL)
    XOR(this->seed_hi, 54, 0xdd894f38e24ec479ULL, 0x6925e83b393340c1ULL)
    XOR(this->seed_hi, 55, 0x9815571c6837338cULL, 0x08ae87e6f15032e1ULL)
    XOR(this->seed_hi, 56, 0x2e34ccc955545d0dULL, 0x380e377d081e988aULL)
    XOR(this->seed_hi, 57, 0xabb6695499088738ULL, 0xfa489750820c64b1ULL)
    XOR(this->seed_hi, 58, 0xd1fc977813aa97f6ULL, 0x8e229c3a401cc040ULL)
    XOR(this->seed_hi, 59, 0xf33a77ab9e804e16ULL, 0x84fc15f114c8ffd5ULL)
    XOR(this->seed_hi, 60, 0x84774bf7aa07a202ULL, 0x5cbb3810d96db47aULL)
    XOR(this->seed_hi, 61, 0xabd2e2e4517a3793ULL, 0x61ba8c6be722c83bULL)
    XOR(this->seed_hi, 62, 0xcb76df4453ed7bf3ULL, 0xf958d44c7154bca9ULL)
    XOR(this->seed_hi, 63, 0x4955857644270dc4ULL, 0xba2b39a94a63bc6fULL)
    this->seed_lo = lo;
    this->seed_hi = hi;
}

unsigned long long rotl(unsigned long long n, int c) {
  return (n << c) | (n >> (64 - c));
}

int stronghold_generator::XrsrRandom::next(int bits) {
    unsigned long long l = this->seed_lo;
    unsigned long long l2 = this->seed_hi;
    unsigned long long r = rotl(l + l2, 17) + l;
    l2 ^= l;
    this->seed_lo = rotl(l, 49) ^ l2 ^ l2 << 21;
    this->seed_hi = rotl(l2, 28);
    return (int)(r >> (64 - bits));
}

int stronghold_generator::XrsrRandom::nextInt() {
    return this->next(32);
}

int stronghold_generator::XrsrRandom::nextInt(int bound) {
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

long long stronghold_generator::XrsrRandom::nextLong() {
    return ((long long)this->next(32) << 32) + (long long)this->next(32);
}

bool stronghold_generator::XrsrRandom::nextBoolean() {
    return this->next(1) != 0;
}

float stronghold_generator::XrsrRandom::nextFloat() {
    return this->next(24) / ((float)(1 << 24));
}

double stronghold_generator::XrsrRandom::nextDouble() {
    return (((long long)(next(26)) << 27) + (long long)next(27)) * 0x1.0p-53;
}

long long stronghold_generator::XrsrRandom::setDecorationSeed(long long worldSeed, int x, int z) {
    this->setSeed(worldSeed);
    long long l2 = this->nextLong() | 1LL;
    long long l3 = this->nextLong() | 1LL;
    long long l4 = (long long)x * l2 + (long long)z * l3 ^ worldSeed;
    this->setSeed(l4);
    return l4;
}

void stronghold_generator::XrsrRandom::setFeatureSeed(long long decorationSeed, int index, int step) {
    long long l2 = decorationSeed + (long long)index + (long long)(10000 * step);
    this->setSeed(l2);
}

void stronghold_generator::XrsrRandom::setLargeFeatureSeed(long long worldSeed, int chunkX, int chunkZ) {
    this->setSeed(worldSeed);
    long long l2 = this->nextLong();
    long long l3 = this->nextLong();
    long long l4 = (long long)chunkX * l2 ^ (long long)chunkZ * l3 ^ worldSeed;
    this->setSeed(l4);
}

void stronghold_generator::XrsrRandom::setLargeFeatureWithSalt(long long worldSeed, int regionX, int regionZ, int salt) {
    long long l2 = (long long)regionX * 341873128712LL + (long long)regionZ * 132897987541LL + worldSeed + (long long)salt;
    this->setSeed(l2);
}