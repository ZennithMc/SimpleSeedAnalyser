"""
stronghold_eyes.py — Port bit-perfect de StrongholdGenerator.cpp (Gaider10/12-eye).

Ce module reproduit EXACTEMENT la génération du stronghold de Minecraft Java
1.18+, en utilisant le même PRNG (Java LCG) et la même BFS avec gestion des
collisions de bounding boxes. Une fois la PortalRoom localisée, les frames
sont calculées avec le même LCG que celui qui a généré la pièce — c'est
l'instant exact où Minecraft fait `nextFloat() > 0.9` pour chaque frame.

API publique :
    count_stronghold_eyes(world_seed, bx, bz) -> int (0..12)
    count_stronghold_frames(world_seed, bx, bz) -> list[bool] de 12 frames
    get_stronghold_portal_pos(world_seed, bx, bz) -> (px, py, pz)

Validation :
    seed=2040984539113960933, stronghold à (1364, 1476)
        -> portal room centre attendu (1371, 1424), eyes attendus 12
    seed=1000, stronghold à (-1404, 1412)
        -> portal room centre attendu (-1445, 1415), eyes attendus 0
"""

from __future__ import annotations
from dataclasses import dataclass

# ──────────────────────────────────────────────────────────────────────────
# Java LCG Random (java.util.Random) — utilisé par tout le générateur
# ──────────────────────────────────────────────────────────────────────────

_MASK48 = (1 << 48) - 1
_MULT   = 0x5DEECE66D
_ADD    = 0xB
_U32    = (1 << 32) - 1
_U64    = (1 << 64) - 1



class _WorkerFailed(RuntimeError):
    """A worker process job raised: the search stops with this error."""

def _to_int32(x: int) -> int:
    """Java cast int (32-bit signed)."""
    x &= _U32
    return x - (1 << 32) if x & 0x80000000 else x


def _to_int64(x: int) -> int:
    """Java cast long (64-bit signed)."""
    x &= _U64
    return x - (1 << 64) if x & 0x8000000000000000 else x


class JavaRandom:
    """Bit-perfect java.util.Random / cubiomes' LCGRandom<0x5DEECE66D, 0xB>."""

    __slots__ = ("seed",)

    def __init__(self, seed: int = 0):
        self.seed = (int(seed) ^ _MULT) & _MASK48

    def setSeed(self, seed: int) -> None:
        self.seed = (int(seed) ^ _MULT) & _MASK48

    def next(self, bits: int) -> int:
        """Returns a signed int (32-bit)."""
        self.seed = (self.seed * _MULT + _ADD) & _MASK48
        # (int)(seed >> (48 - bits)) — Java cast to int = sign-extend
        raw = self.seed >> (48 - bits)
        if bits == 32:
            return _to_int32(raw)
        return raw  # bits<32: always non-negative (top bit is 0)

    def nextInt_bound(self, bound: int) -> int:
        """nextInt(bound), bound>0."""
        if bound <= 0:
            raise ValueError("bound must be positive")
        m = bound - 1
        # Power of 2
        if (bound & m) == 0:
            r = self.next(31)
            # (int)((bound * (long)r) >> 31)
            v = (bound * r) >> 31
            return _to_int32(v)
        # Reject loop
        while True:
            u = self.next(31)
            r = u % bound
            if u - r + m >= 0:
                return r

    def nextLong(self) -> int:
        """nextLong(): ((long)next(32) << 32) + (long)next(32) — Java signed."""
        hi = self.next(32)
        lo = self.next(32)
        # In Java, (long)next(32) sign-extends. Then (hi << 32) + lo where lo
        # is also (long) sign-extended. The sum can overflow but we want exact
        # 64-bit signed semantics.
        result = ((hi & _U64) << 32) + (lo & _U64)
        return _to_int64(result & _U64)

    def nextBoolean(self) -> bool:
        return self.next(1) != 0

    def nextFloat(self) -> float:
        return self.next(24) / float(1 << 24)

    def next_float(self) -> float:
        return self.next(24) / float(1 << 24)

    def next_long(self) -> int:
        return self.nextLong()

    def nextDouble(self) -> float:
        # ((long)next(26) << 27 + (long)next(27)) * 2^-53
        hi = self.next(26)
        lo = self.next(27)
        return (((hi & _U64) << 27) + (lo & _U64)) * (2.0 ** -53)

    def setLargeFeatureSeed(self, world_seed: int, chunk_x: int, chunk_z: int) -> None:
        """Same as in LCGRandom: seed by (chunk_x*l2 ^ chunk_z*l3 ^ ws)."""
        self.setSeed(world_seed)
        l2 = self.nextLong()
        l3 = self.nextLong()
        l4 = (chunk_x * l2) ^ (chunk_z * l3) ^ world_seed
        # Need 64-bit truncation for the seed
        self.setSeed(_to_int64(l4 & _U64))


# ──────────────────────────────────────────────────────────────────────────
# Direction enum + constants
# ──────────────────────────────────────────────────────────────────────────

NORTH = 0
SOUTH = 1
WEST  = 2
EAST  = 3
HORIZONTAL = (NORTH, EAST, SOUTH, WEST)  # comme Direction.Plane.HORIZONTAL en MC

# PieceType
STRAIGHT = 0
PRISON_HALL = 1
LEFT_TURN = 2
RIGHT_TURN = 3
ROOM_CROSSING = 4
STRAIGHT_STAIRS_DOWN = 5
STAIRS_DOWN = 6
FIVE_CROSSING = 7
CHEST_CORRIDOR = 8
LIBRARY = 9
PORTAL_ROOM = 10
FILLER_CORRIDOR = 11
PT_NONE = -1

# (weight, maxPlaceCount, minDepth) — order matches PieceType enum
PIECE_WEIGHTS = (
    (40, 0, 0),  # STRAIGHT
    ( 5, 5, 0),  # PRISON_HALL
    (20, 0, 0),  # LEFT_TURN
    (20, 0, 0),  # RIGHT_TURN
    (10, 6, 0),  # ROOM_CROSSING
    ( 5, 5, 0),  # STRAIGHT_STAIRS_DOWN
    ( 5, 5, 0),  # STAIRS_DOWN
    ( 5, 4, 0),  # FIVE_CROSSING
    ( 5, 4, 0),  # CHEST_CORRIDOR
    (10, 2, 5),  # LIBRARY
    (20, 1, 6),  # PORTAL_ROOM
)


# ──────────────────────────────────────────────────────────────────────────
# BoundingBox
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class BoundingBox:
    minX: int; minY: int; minZ: int
    maxX: int; maxY: int; maxZ: int

    def intersects(self, b: "BoundingBox") -> bool:
        return (self.maxX >= b.minX and self.minX <= b.maxX and
                self.maxZ >= b.minZ and self.minZ <= b.maxZ and
                self.maxY >= b.minY and self.minY <= b.maxY)


def orient_box(x: int, y: int, z: int,
               offsetW: int, offsetH: int, offsetD: int,
               width: int, height: int, depth: int,
               direction: int) -> BoundingBox:
    """Reproduit BoundingBox::orientBox bit-perfect."""
    if direction == NORTH:
        return BoundingBox(x + offsetW,         y + offsetH, z - depth + 1 + offsetD,
                           x + width - 1 + offsetW, y + height - 1 + offsetH, z + offsetD)
    if direction == SOUTH:
        return BoundingBox(x + offsetW,         y + offsetH, z + offsetD,
                           x + width - 1 + offsetW, y + height - 1 + offsetH, z + depth - 1 + offsetD)
    if direction == WEST:
        return BoundingBox(x - depth + 1 + offsetD, y + offsetH, z + offsetW,
                           x + offsetD,            y + height - 1 + offsetH, z + width - 1 + offsetW)
    if direction == EAST:
        return BoundingBox(x + offsetD,            y + offsetH, z + offsetW,
                           x + depth - 1 + offsetD, y + height - 1 + offsetH, z + width - 1 + offsetW)
    return BoundingBox(0, 0, 0, 0, 0, 0)


def make_bbox_for_start(x: int, y: int, z: int, direction: int,
                          width: int, height: int, depth: int) -> BoundingBox:
    """Piece::makeBoundingBox — pour la pièce START seulement."""
    if direction in (NORTH, SOUTH):
        return BoundingBox(x, y, z, x + width - 1, y + height - 1, z + depth - 1)
    return BoundingBox(x, y, z, x + depth - 1, y + height - 1, z + width - 1)


# ──────────────────────────────────────────────────────────────────────────
# Piece — léger, juste ce qu'il faut
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class Piece:
    type: int
    depth: int
    bbox: BoundingBox
    orientation: int
    additionalData: int

    def get_world_x(self, offsetW: int, offsetD: int) -> int:
        if self.orientation in (NORTH, SOUTH):
            return self.bbox.minX + offsetW
        if self.orientation == WEST:
            return self.bbox.maxX - offsetD
        # EAST
        return self.bbox.minX + offsetD

    def get_world_z(self, offsetW: int, offsetD: int) -> int:
        if self.orientation == NORTH:
            return self.bbox.maxZ - offsetD
        if self.orientation == SOUTH:
            return self.bbox.minZ + offsetD
        # WEST or EAST
        return self.bbox.minZ + offsetW

    def get_world_y(self, offsetH: int) -> int:
        return self.bbox.minY + offsetH


# ──────────────────────────────────────────────────────────────────────────
# StrongholdGenerator — port direct
# ──────────────────────────────────────────────────────────────────────────

class StrongholdGenerator:
    """Port Python bit-perfect de StrongholdGenerator.cpp (Gaider)."""

    def __init__(self):
        self.reset_pieces()
        self.startX = 0
        self.startZ = 0
        # Version du FillerCorridor : True = 1.13-1.17, False = 1.18+ (defaut).
        # FillerCorridor.findPieceBox a change entre 1.17 et 1.18, ce qui
        # decale l'etat RNG a moveBelowSeaLevel (donc le Y du donjon).
        self.pre118 = False

    def reset_pieces(self):
        # piecePlaceCounts est une liste de [pieceType, placeCount]
        # par défaut, tous types présents avec count=0 dans l'ordre du enum
        self.piece_place_counts = [[i, 0] for i in range(11)]
        self.imposed = PT_NONE
        self.total_weight = 145
        self.previous = PT_NONE
        self.pieces: list[Piece] = []
        self.pending: list[int] = []   # indices into pieces
        self.portal_room_piece: Piece | None = None
        self.gen_stopped = False
        # Pour récupérer l'état RNG juste après création du PortalRoom
        self._portal_rng_state = None

    def update_piece_weight(self):
        for entry in self.piece_place_counts:
            pt = entry[0]
            cnt = entry[1]
            w, maxc, _ = PIECE_WEIGHTS[pt]
            if maxc > 0 and cnt < maxc:
                return
        self.gen_stopped = True

    @staticmethod
    def is_ok_box(bb: BoundingBox) -> bool:
        return bb.minY > 10

    def find_collision(self, bb: BoundingBox) -> Piece | None:
        for p in self.pieces:
            if p.bbox.intersects(bb):
                return p
        return None

    def create_piece_bbox(self, pt: int, x: int, y: int, z: int,
                           direction: int) -> BoundingBox:
        if pt == STRAIGHT:
            return orient_box(x, y, z, -1, -1, 0, 5, 5, 7, direction)
        if pt == PRISON_HALL:
            return orient_box(x, y, z, -1, -1, 0, 9, 5, 11, direction)
        if pt == LEFT_TURN:
            return orient_box(x, y, z, -1, -1, 0, 5, 5, 5, direction)
        if pt == RIGHT_TURN:
            return orient_box(x, y, z, -1, -1, 0, 5, 5, 5, direction)
        if pt == ROOM_CROSSING:
            return orient_box(x, y, z, -4, -1, 0, 11, 7, 11, direction)
        if pt == STRAIGHT_STAIRS_DOWN:
            return orient_box(x, y, z, -1, -7, 0, 5, 11, 8, direction)
        if pt == STAIRS_DOWN:
            return orient_box(x, y, z, -1, -7, 0, 5, 11, 5, direction)
        if pt == FIVE_CROSSING:
            return orient_box(x, y, z, -4, -3, 0, 10, 9, 11, direction)
        if pt == CHEST_CORRIDOR:
            return orient_box(x, y, z, -1, -1, 0, 5, 5, 7, direction)
        if pt == LIBRARY:
            return orient_box(x, y, z, -4, -1, 0, 14, 11, 15, direction)
        if pt == PORTAL_ROOM:
            return orient_box(x, y, z, -4, -1, 0, 11, 8, 16, direction)
        return BoundingBox(0, 0, 0, 0, 0, 0)

    def create_piece(self, pt: int, rng: JavaRandom, direction: int,
                      depth: int, bb: BoundingBox) -> Piece:
        """Reproduit createPiece() — le RNG est consommé selon le type."""
        # Tous les types consomment au minimum 1× nextInt(5) sauf PORTAL_ROOM
        # qui n'en consomme aucune (cf. source !).
        # Vérification de la source : OUI, PORTAL_ROOM consomme aussi nextInt(5).
        if pt == STRAIGHT:
            rng.nextInt_bound(5)
            ad = 0
            ad |= (1 if rng.nextInt_bound(2) == 0 else 0) << 0
            ad |= (1 if rng.nextInt_bound(2) == 0 else 0) << 1
            return Piece(pt, depth, bb, direction, ad)
        if pt in (PRISON_HALL, LEFT_TURN, RIGHT_TURN,
                  STRAIGHT_STAIRS_DOWN, STAIRS_DOWN, CHEST_CORRIDOR, LIBRARY):
            rng.nextInt_bound(5)
            return Piece(pt, depth, bb, direction, 0)
        if pt == ROOM_CROSSING:
            rng.nextInt_bound(5)
            rng.nextInt_bound(5)
            return Piece(pt, depth, bb, direction, 0)
        if pt == FIVE_CROSSING:
            rng.nextInt_bound(5)
            ad = 0
            ad |= (1 if rng.nextBoolean() else 0) << 0
            ad |= (1 if rng.nextBoolean() else 0) << 1
            ad |= (1 if rng.nextBoolean() else 0) << 2
            ad |= (1 if rng.nextInt_bound(3) > 0 else 0) << 3
            return Piece(pt, depth, bb, direction, ad)
        if pt == PORTAL_ROOM:
            # On enregistre l'état EXACT après création (les frames seront
            # consommées plus tard par PortalRoom::postProcess).
            # IMPORTANT : PortalRoom dans Gaider's createPiece() ne consomme
            # PAS de RNG du tout. C'est dans postProcess() que les 12 frames
            # sont tirées. Mais postProcess est appelé APRÈS la BFS complète
            # avec un RNG dédié au chunk de la pièce... À tester.
            piece = Piece(pt, depth, bb, direction, 0)
            self.portal_room_piece = piece
            self.gen_stopped = True
            return piece
        # FILLER_CORRIDOR n'est jamais créé via create_piece
        return Piece(pt, depth, bb, direction, 0)

    def add_piece(self, p: Piece):
        idx = len(self.pieces)
        self.pieces.append(p)
        self.pending.append(idx)

    def find_and_create(self, pt: int, rng: JavaRandom, x: int, y: int, z: int,
                         direction: int, depth: int) -> bool:
        """findAndCreatePieceFactory — gère les collisions, place la pièce ou pas."""
        bb = self.create_piece_bbox(pt, x, y, z, direction)
        if (not self.is_ok_box(bb)) or (self.find_collision(bb) is not None):
            if pt == LIBRARY:
                # Tente la version réduite de la library
                bb = orient_box(x, y, z, -4, -1, 0, 14, 6, 15, direction)
                if (not self.is_ok_box(bb)) or (self.find_collision(bb) is not None):
                    return False
            else:
                return False
        p = self.create_piece(pt, rng, direction, depth, bb)
        self.add_piece(p)
        return True

    def gen_piece_from_small_door(self, rng: JavaRandom, x: int, y: int, z: int,
                                    direction: int, depth: int):
        if self.gen_stopped:
            return

        if self.imposed != PT_NONE:
            imp = self.imposed
            self.imposed = PT_NONE
            if self.find_and_create(imp, rng, x, y, z, direction, depth):
                return

        # 5 tentatives
        for _attempt in range(5):
            sel = rng.nextInt_bound(self.total_weight)
            for i, entry in enumerate(self.piece_place_counts):
                pt = entry[0]
                w, maxc, mind = PIECE_WEIGHTS[pt]
                sel -= w
                if sel >= 0:
                    continue
                # Found candidate
                if (maxc != 0 and entry[1] >= maxc) or (depth < mind) or (pt == self.previous):
                    break  # nextAttempt
                if not self.find_and_create(pt, rng, x, y, z, direction, depth):
                    continue  # try next loop iter (check next type)
                entry[1] += 1
                self.previous = pt
                # if !isValid → remove from list, decrease totalWeight, updatePieceWeight
                if maxc != 0 and entry[1] >= maxc:
                    self.total_weight -= w
                    del self.piece_place_counts[i]
                    self.update_piece_weight()
                return
            # If we broke without `return`, try another attempt
            continue

        # 5 tentatives échouées : tente un FILLER_CORRIDOR.
        # FillerCorridor.findPieceBox differe entre versions :
        #   1.13-1.17 (pre118) : for i=3..1, teste depth=i-1, retourne depth=i
        #   1.18+              : for i=2..1, teste depth=i,   retourne depth=i+1
        bb = orient_box(x, y, z, -1, -1, 0, 5, 5, 4, direction)
        coll = self.find_collision(bb)
        if coll is None:
            return
        if coll.bbox.minY != bb.minY:
            return
        if self.pre118:
            for i in range(3, 0, -1):
                bb_test = orient_box(x, y, z, -1, -1, 0, 5, 5, i - 1, direction)
                if coll.bbox.intersects(bb_test):
                    continue
                bb_filler = orient_box(x, y, z, -1, -1, 0, 5, 5, i, direction)
                if bb_filler.minY > 1:
                    self.add_piece(Piece(FILLER_CORRIDOR, depth, bb_filler, direction, 0))
                return
        else:
            for i in range(2, 0, -1):
                bb_i = orient_box(x, y, z, -1, -1, 0, 5, 5, i, direction)
                if coll.bbox.intersects(bb_i):
                    continue
                bb_filler = orient_box(x, y, z, -1, -1, 0, 5, 5, i + 1, direction)
                if bb_filler.minY > 1:
                    self.add_piece(Piece(FILLER_CORRIDOR, depth, bb_filler, direction, 0))
                return

    def gen_and_add(self, rng: JavaRandom, x: int, y: int, z: int,
                     direction: int, depth: int):
        if depth > 50:
            return
        if abs(x - self.startX) > 112 or abs(z - self.startZ) > 112:
            return
        self.gen_piece_from_small_door(rng, x, y, z, direction, depth + 1)

    # gen forward / left / right children — direct ports
    def child_forward(self, p: Piece, rng: JavaRandom, n: int, n2: int):
        d = p.orientation
        bb = p.bbox
        if d == NORTH:
            return self.gen_and_add(rng, bb.minX + n, bb.minY + n2, bb.minZ - 1, NORTH, p.depth)
        if d == SOUTH:
            return self.gen_and_add(rng, bb.minX + n, bb.minY + n2, bb.maxZ + 1, SOUTH, p.depth)
        if d == WEST:
            return self.gen_and_add(rng, bb.minX - 1, bb.minY + n2, bb.minZ + n, WEST, p.depth)
        if d == EAST:
            return self.gen_and_add(rng, bb.maxX + 1, bb.minY + n2, bb.minZ + n, EAST, p.depth)

    def child_left(self, p: Piece, rng: JavaRandom, n: int, n2: int):
        d = p.orientation
        bb = p.bbox
        if d in (NORTH, SOUTH):
            return self.gen_and_add(rng, bb.minX - 1, bb.minY + n, bb.minZ + n2, WEST, p.depth)
        if d in (WEST, EAST):
            return self.gen_and_add(rng, bb.minX + n2, bb.minY + n, bb.minZ - 1, NORTH, p.depth)

    def child_right(self, p: Piece, rng: JavaRandom, n: int, n2: int):
        d = p.orientation
        bb = p.bbox
        if d in (NORTH, SOUTH):
            return self.gen_and_add(rng, bb.maxX + 1, bb.minY + n, bb.minZ + n2, EAST, p.depth)
        if d in (WEST, EAST):
            return self.gen_and_add(rng, bb.minX + n2, bb.minY + n, bb.maxZ + 1, SOUTH, p.depth)

    def add_children(self, p: Piece, rng: JavaRandom):
        t = p.type
        if t == STRAIGHT:
            self.child_forward(p, rng, 1, 1)
            if (p.additionalData & (1 << 0)) != 0:
                self.child_left(p, rng, 1, 2)
            if (p.additionalData & (1 << 1)) != 0:
                self.child_right(p, rng, 1, 2)
        elif t == PRISON_HALL:
            self.child_forward(p, rng, 1, 1)
        elif t == LEFT_TURN:
            if p.orientation in (NORTH, EAST):
                self.child_left(p, rng, 1, 1)
            else:
                self.child_right(p, rng, 1, 1)
        elif t == RIGHT_TURN:
            if p.orientation in (NORTH, EAST):
                self.child_right(p, rng, 1, 1)
            else:
                self.child_left(p, rng, 1, 1)
        elif t == ROOM_CROSSING:
            self.child_forward(p, rng, 4, 1)
            self.child_left(p, rng, 1, 4)
            self.child_right(p, rng, 1, 4)
        elif t == STRAIGHT_STAIRS_DOWN:
            self.child_forward(p, rng, 1, 1)
        elif t == STAIRS_DOWN:
            if p.additionalData != 0:
                self.imposed = FIVE_CROSSING
            self.child_forward(p, rng, 1, 1)
        elif t == FIVE_CROSSING:
            n = 3
            n2 = 5
            d = p.orientation
            if d in (WEST, NORTH):
                n = 8 - n
                n2 = 8 - n2
            self.child_forward(p, rng, 5, 1)
            if (p.additionalData & (1 << 0)) != 0:
                self.child_left(p, rng, n, 1)
            if (p.additionalData & (1 << 1)) != 0:
                self.child_left(p, rng, n2, 7)
            if (p.additionalData & (1 << 2)) != 0:
                self.child_right(p, rng, n, 1)
            if (p.additionalData & (1 << 3)) != 0:
                self.child_right(p, rng, n2, 7)
        elif t == CHEST_CORRIDOR:
            self.child_forward(p, rng, 1, 1)
        # LIBRARY, PORTAL_ROOM, FILLER_CORRIDOR: aucun enfant

    def generate(self, world_seed: int, chunk_x: int, chunk_z: int):
        """Reproduit StrongholdGenerator::generate(). Boucle sur attempts
        jusqu'à trouver le portal_room (toujours trouvé en théorie)."""
        attempt = 0
        rng = JavaRandom(0)
        while True:
            self.reset_pieces()
            rng.setLargeFeatureSeed(_to_int64((world_seed + attempt) & _U64),
                                     chunk_x, chunk_z)
            attempt += 1
            x = (chunk_x << 4) + 2
            z = (chunk_z << 4) + 2
            self.startX = x
            self.startZ = z
            direction = HORIZONTAL[rng.nextInt_bound(4)]
            bbox = make_bbox_for_start(x, 64, z, direction, 5, 11, 5)
            start_piece = Piece(STAIRS_DOWN, 0, bbox, direction, 1)
            self.pieces.append(start_piece)
            self.start_direction = direction
            self.add_children(start_piece, rng)
            # BFS
            while self.pending and not self.gen_stopped:
                i = rng.nextInt_bound(len(self.pending))
                idx = self.pending.pop(i)
                self.add_children(self.pieces[idx], rng)
            if self.portal_room_piece is not None:
                self.last_attempt = attempt
                return


# ──────────────────────────────────────────────────────────────────────────
# Frame counting — pipeline EXACT de Minecraft 1.18+ pour le portail.
# ──────────────────────────────────────────────────────────────────────────
#
# Source : Gaider10/12-eye/src/12eye.cu, fonction test_world_seed_skip().
#
#   uint64_t decoration_seed = xrsr128_getDecorationSeed(world_seed,
#                                                        chunk_x, chunk_z);
#   XRSR128 rng;
#   xrsr_seed(&rng, decoration_seed + OFFSET);   // setSeed (constructor)
#   rng = skipPortalRoom(rng);                   // skip RNG calls fixe
#   for (int j = 0; j < 12; j++)
#       aboolean[j] = rng.nextFloat() > 0.9F;   // = nextLong < threshold
#
# OFFSET = setFeatureSeed(decoration_seed, index, step) = index + 10000*step,
# and it is VERSION-DEPENDENT (see _eye_feature_offset / _eye_rng_for_chunk):
#   1.18.0/1.18.1 : stronghold step STRONGHOLDS(5), index 0 -> +50000.
#   1.19+         : step SURFACE_STRUCTURES(4), index 19 (default) -> +40019.
# Gaider10's reference (12eye.cu) used +40019, which is only right for 1.19+;
# 1.18.1 needs +50000 (verified vs the 1.18.1 source + in game).
#
# `chunk_x, chunk_z` est le chunk où la PORTAL_ROOM se trouve. La PORTAL_ROOM
# peut chevaucher 2 chunks ; MC traite chaque chunk indépendamment et
# n'écrit les frames que pour le chunk où la pièce est traitée. Selon
# Gaider, le chunk valide est celui où la PORTAL_ROOM est l'unique pièce
# du stronghold présente (les chunks où d'autres pièces sont également
# présentes ne sont pas le "premier chunk" pour cette piece).


# ── XoRoShiRo128++ — utilisé par MC pour decoration_seed et postProcess ──

_XR_SILVER = 0x6A09E667F3BCC909
_XR_GOLDEN = 0x9E3779B97F4A7C15
_XR_MIX1   = 0xBF58476D1CE4E5B9
_XR_MIX2   = 0x94D049BB133111EB


def _xr_mix64(z: int) -> int:
    z = ((z ^ (z >> 30)) * _XR_MIX1) & _U64
    z = ((z ^ (z >> 27)) * _XR_MIX2) & _U64
    return (z ^ (z >> 31)) & _U64


def _xr_rotl(x: int, k: int) -> int:
    return ((x << k) | (x >> (64 - k))) & _U64


class Xrsr128:
    """XoRoShiRo128++ — RNG MC 1.18+. Seedé par xrsr_seed (constructor)."""

    __slots__ = ("lo", "hi")

    def __init__(self, seed: int = 0):
        self.set_seed(seed)

    def set_seed(self, seed: int) -> None:
        s = (int(seed) & _U64) ^ _XR_SILVER
        self.lo = _xr_mix64(s)
        self.hi = _xr_mix64((s + _XR_GOLDEN) & _U64)
        # Si (lo|hi)==0 → fixed bootstrap ; jamais en pratique avec MC.
        if (self.lo | self.hi) == 0:
            self.lo = (1 << 64) - 7046029254386353131  # 0x9E6D62D0E69CFEED
            self.hi = _XR_SILVER

    def _xrsr_long(self) -> int:
        """Comme xrsr_long() en C : retourne uint64 puis next."""
        lo, hi = self.lo, self.hi
        s = (lo + hi) & _U64
        r = (_xr_rotl(s, 17) + lo) & _U64
        # next : hi ^= lo, lo = rotl(lo,49) ^ hi ^ (hi<<21), hi = rotl(hi,28)
        hi_xor = hi ^ lo
        new_lo = (_xr_rotl(lo, 49) ^ hi_xor ^ ((hi_xor << 21) & _U64)) & _U64
        new_hi = _xr_rotl(hi_xor, 28)
        self.lo, self.hi = new_lo, new_hi
        return r

    def next(self, bits: int) -> int:
        """next(bits) — comme dans XrsrRandom.cpp : (int)(xrsr_long() >> (64-bits))."""
        # Java cast int = sign-extend
        r = self._xrsr_long()
        v = r >> (64 - bits)
        return _to_int32(v) if bits == 32 else v

    def next_float(self) -> float:
        return self.next(24) / float(1 << 24)

    def next_long(self) -> int:
        """nextLong() — match C exactly :
            ((long long)next(32) << 32) + (long long)next(32)
        Le cast (long long) sign-extend les `int` négatifs.
        """
        hi32 = self.next(32)   # signed int, peut être négatif
        lo32 = self.next(32)   # idem
        # En C : (long long)hi32 << 32 :
        #   si hi32 >= 0 : juste hi32 << 32
        #   si hi32 < 0  : 0xFFFFFFFF........ (sign-extend) puis << 32
        # Plus simple : on garde hi32 en signed Python (déjà sign-extended
        # implicitement par _to_int32 dans next()). On shift et on additionne
        # lo32 (aussi signed). Le résultat doit ensuite être tronqué à 64 bits
        # signés pour matcher overflow C.
        result = ((hi32 << 32) + lo32)
        return _to_int64(result & _U64)


def _xrsr_seed_lo(seed: int) -> int:
    return _xr_mix64((int(seed) & _U64) ^ _XR_SILVER)


def _xrsr_seed_hi(seed: int) -> int:
    return _xr_mix64(((int(seed) & _U64) ^ _XR_SILVER) + _XR_GOLDEN & _U64)


def _xrsr_get_decoration_seed(world_seed: int, chunk_x: int, chunk_z: int) -> int:
    """
    Reproduit xrsr128_getDecorationSeed(world_seed, chunk_x, chunk_z).

    Note : chunk_x et chunk_z sont en CHUNKS, et le code multiplie par 16
    (<<4) pour avoir les coords de bloc.
    """
    rng = Xrsr128(world_seed)
    a = rng.next_long() | 1
    b = rng.next_long() | 1
    block_x = chunk_x << 4
    block_z = chunk_z << 4
    # En C : (chunk_x<<4) * a + (chunk_z<<4) * b ^ world_seed
    # Note : ^ a une priorité plus basse que +/* en C — donc :
    #   ((chunk_x<<4)*a + (chunk_z<<4)*b) ^ world_seed
    val = ((block_x * a) + (block_z * b)) & _U64
    val ^= int(world_seed) & _U64
    return _to_int64(val)


# ── skipPortalRoom : matrice XOR pré-calculée (auto-extraite de Gaider) ──

_XOR_LO = (
    (0xbea1cb0e235828b3, 0x9729b1f34956cf87),
    (0x30d9b3a637b8cc30, 0x69438152e6e4137b),
    (0xef9ba2a91df9826a, 0x54d7aa11544d7f0d),
    (0x6b77a2a23f77b567, 0x184dd2690f183868),
    (0x8ee1a0478680083a, 0x66cb946dcd298822),
    (0x7396c0e3bb0fca9a, 0x715a6f94782b3af2),
    (0x9386458f6709c14a, 0xa57b30c71d13dd35),
    (0x77b6ca6dbf1381b0, 0x860757166f353f18),
    (0xe98f1e3a0e68ee6b, 0x2eb84f434773c0f0),
    (0x48a8feb481412058, 0x6ec4a811d3f5e13a),
    (0x2acb074fd1528435, 0x310cb9f1bb763692),
    (0x07cfecc893741e1e, 0x54445aa20b269e02),
    (0x175f9549f77a39ad, 0xe9066c93af9dfdb0),
    (0x4426c48954682e38, 0x62c2b5e8e0d69c9b),
    (0x06eeb208c90ce28c, 0x5e5ef7e00ed35ea9),
    (0xb20ad12841018041, 0xb1b3fcfb522ae01b),
    (0xf94466f60cc9cfd9, 0xfc7cf254b6da0494),
    (0x7420dd36650b4e03, 0xe6ee807dd4f693c4),
    (0xa4d5e84842496c56, 0x7ae8e208f7190436),
    (0x0a1651d2513089ca, 0xd1ea97de107b32c9),
    (0xfa7bfb681850a9a3, 0x5f5291a6283d5e3b),
    (0xef063377f32c1b27, 0xe075e04ebb0cd1a4),
    (0x8ee415201f9ac0d7, 0x4a94290c864c26ab),
    (0x59d29bb6ecd74a35, 0x5e5ddf9a3ab76ecc),
    (0x70d37d8062ede900, 0x80b8f5bd4f4296e2),
    (0xc325d770d103a272, 0x14babf1d5d7b1bf1),
    (0x1386ef197be907e6, 0xfe7068c56f7362b3),
    (0x378500ecc90a9cf1, 0xd22c04459a7949c0),
    (0xfe49609deaa81ae4, 0x45f33f678cfa46df),
    (0xc8df216f835c4cdc, 0x0cbf3a8b7a964ff5),
    (0xadf96f98af5a1ec9, 0xb0c0a793d68846d9),
    (0x814f25287c709c9b, 0x148a61faa04a5b72),
    (0xe0165b6fcb8e09f0, 0xa500b82a5dd0442f),
    (0xa559a881ba91e661, 0x4ab3bd9ea43a43fc),
    (0x828686ae4bf3b1e7, 0x10a26e2069b47539),
    (0x354a4970c3798857, 0x6617a2470d9126f2),
    (0x627399b8aeff223b, 0xc8cb1f7ff71d7799),
    (0xf5dc48949583aab8, 0xf5da697d4650b5b4),
    (0x20b68ce2799464a4, 0xaed50dfa8e1f2c0a),
    (0x6ce16fa557508398, 0x00621d3359224c5d),
    (0xf5437e0d460fb080, 0xdd0d3cab187ad8cd),
    (0x5a6784625179eb67, 0x0a7dff3ceca8340a),
    (0x5bada798d37bf1da, 0x66c0ea498ae30744),
    (0xf49f3202af8b828d, 0x9649d0792a7c0f4d),
    (0x06a6f88edfd5f696, 0xdc21a06b0df2c4d7),
    (0xa958de14f8755f8e, 0x5c36949458eb5ff4),
    (0xf41b985d37852aca, 0x67840ae2d8980411),
    (0x0889eabc540cb5fc, 0x1002322c32d607c3),
    (0x2df8ffea481d069a, 0xd08621bb396c6752),
    (0x323e4d1e502fcc2e, 0xd677a3a5fbcf0701),
    (0x2a1b5a014f1e28d6, 0x8fbc61e11fb583e8),
    (0x79920c49fc2295fe, 0x1eb45493f04b9382),
    (0xa6f2980b77654919, 0xd5706cc1e0b7274e),
    (0x49242973f0d38bc5, 0xbaae62ca0da195ee),
    (0x5bb41874775c1cf5, 0x9a1086f162376218),
    (0xa1c27475a00c0d56, 0x5182ac806d5cecef),
    (0xfa8abd84ca3bf283, 0xfb2ef04531097121),
    (0xca58f43d9cb60c51, 0xac19179c3d689655),
    (0x4a258cc0cdea0d50, 0xed43af5dd7af7acc),
    (0xcc1823d552f2c042, 0x468128efb2d5f96c),
    (0xedc3c15e60d7a055, 0xb25a4a5386305762),
    (0x9b3edfebd022f75f, 0x29041dcb27c38d24),
    (0xb4a161f08d70845a, 0xc64435382757e8c5),
    (0x1476f5e55753c1e6, 0x66d8209886988ee9),
)
_XOR_HI = (
    (0x32b37ba8ebcebd21, 0x19c7c3b6a2248bf0),
    (0xac20e08487f30521, 0x04585f12f71a7a7e),
    (0x3a04800b2f57d6c2, 0x4361d754ffc3ef3a),
    (0x9116ff8703a2fde4, 0x94781ed65ce4f05c),
    (0xc8bf201295aa0187, 0x2dada86c4c4116a0),
    (0x169d177d72e1cf6e, 0x6190775f0a84b841),
    (0x6dc7ddf8aef47810, 0x5bfa7ddac8c92335),
    (0xb2892a43430ef64e, 0xe0879f073b8390aa),
    (0x935baac54121cfc4, 0x9f0c3ea0adb5a286),
    (0x0ea8518bf879a5ef, 0xf664d22a0895a881),
    (0x8ba56f98f2cc4f7a, 0xbf4ed50a596ac088),
    (0xeef21af06e0a0185, 0xb003a9c42a64947f),
    (0x9fb641777d5b0d1e, 0x63c0cd54965137be),
    (0x0a06c702e598e322, 0xfbeaf3c81b304e89),
    (0x1a5ba9c2663dd33f, 0x37d7f24be68d314f),
    (0xbb27059694afd248, 0x8d1ca37e6d9802f0),
    (0xfa53f67a95decced, 0x84e7a1db22563dc3),
    (0xf6458473367f4bda, 0xbb13ff967e38bc8c),
    (0x6603c03ffc16d685, 0x4f459c09fe3c94c6),
    (0x085404637907b059, 0xbba841c7d6560f22),
    (0xbf5e11dcbb585d38, 0xaa8bd2b5b259a49b),
    (0x8be8ba0509a9e3b1, 0xf4077d9579ed71c4),
    (0x6f1ee9bba817ede4, 0x5a56ce904aadad43),
    (0x64bca8c9658ea667, 0x85bd0a5b2b1f613e),
    (0x9af56039ed5b676b, 0x7e8ced804a7bf3c1),
    (0x605b50115c8471b7, 0xa2ec7b2e7c4cbcc9),
    (0x368afd852e5264ea, 0xec3d9bb94636cd36),
    (0xe492beffe8642a2b, 0x76982087ceeeab85),
    (0xb5395b14c2c37815, 0x4801e76ae6fd6cbb),
    (0xd9333173d2a5871e, 0x8d9d02574584a4bb),
    (0x4b407a8076b89d2a, 0x4661b6902fc7d7be),
    (0x0e50bff9ddccb272, 0x570906b495b01afb),
    (0xa05ac64529d282cf, 0xb5f8c537fd811c76),
    (0x4796788837961b7e, 0x8ceaa8fd64633a67),
    (0x826090d1fd6087b9, 0x034ce4ff7e425e42),
    (0xce777c9e109b7000, 0x18200d9679cc6824),
    (0x7f997981adf1dbf1, 0xe7e04d380470b92c),
    (0xfa5f51fac197f6ab, 0x03f57d6bfc05bba6),
    (0x813e4a988e6ca307, 0xc22968d5e4ce50c7),
    (0xa39f5ef8fd150cdd, 0xe1c2bc69fe501b1e),
    (0x8cb69e62422cb70e, 0xb4c2fbe82e04a84e),
    (0x694ac8fda0855c93, 0x87c0cdc2253a7269),
    (0xabaff55f08adaf63, 0x066b779d29655731),
    (0x5fea359b6c9d1814, 0x97bd79af0dfc9c0a),
    (0x17e0a1707ea1201b, 0xf8a33459217404a2),
    (0x00122ea1784a4d19, 0x00dd88bb0742b4e9),
    (0xa81623c9278d8a0d, 0xfc8078cedc62f651),
    (0xb5a03428ac00fd10, 0x15cb73b6cbbff9d3),
    (0x308c609078741ef2, 0x64ecd8db5c9ff721),
    (0xb3afddc877a91684, 0xe8dd2d0bf421b74f),
    (0xc4508328c345c028, 0x566f3dbbc74afc1a),
    (0x7b9d7f4dad12a68a, 0xf349042186423b7c),
    (0x43f9aade34df2e55, 0x6841f59bfec9c94b),
    (0xc532c3fa0aadfa0b, 0xaffec201fcb72a07),
    (0xdd894f38e24ec479, 0x6925e83b393340c1),
    (0x9815571c6837338c, 0x08ae87e6f15032e1),
    (0x2e34ccc955545d0d, 0x380e377d081e988a),
    (0xabb6695499088738, 0xfa489750820c64b1),
    (0xd1fc977813aa97f6, 0x8e229c3a401cc040),
    (0xf33a77ab9e804e16, 0x84fc15f114c8ffd5),
    (0x84774bf7aa07a202, 0x5cbb3810d96db47a),
    (0xabd2e2e4517a3793, 0x61ba8c6be722c83b),
    (0xcb76df4453ed7bf3, 0xf958d44c7154bca9),
    (0x4955857644270dc4, 0xba2b39a94a63bc6f),
)


def _skip_portal_room(rng: Xrsr128) -> None:
    """
    Reproduit XrsrRandom::skipPortalRoom() bit-perfect.
    Avance le RNG d'un nombre fixe d'appels qui correspond aux opérations
    consommées par PortalRoom::postProcess avant les 12 frames.

    Implémentation : matrice XOR de Gaider, avec start point (lo=0, hi=0).
    """
    seed_lo = rng.lo
    seed_hi = rng.hi
    new_lo = 0
    new_hi = 0
    for i, (him, lom) in enumerate(_XOR_LO):
        if (seed_lo >> i) & 1:
            new_hi ^= him
            new_lo ^= lom
    for i, (him, lom) in enumerate(_XOR_HI):
        if (seed_hi >> i) & 1:
            new_hi ^= him
            new_lo ^= lom
    rng.lo = new_lo & _U64
    rng.hi = new_hi & _U64


# ──────────────────────────────────────────────────────────────────────────
# Identifier le(s) chunk(s) de la PortalRoom
# ──────────────────────────────────────────────────────────────────────────

def _find_portal_chunk_candidates(gen: StrongholdGenerator) -> list[tuple[int, int]]:
    """
    Retourne tous les chunks where the PortalRoom est l'unique pièce du
    stronghold présente (`is_first=true`). Range scannée = bbox complète
    de la PortalRoom.
    """
    p = gen.portal_room_piece
    if p is None:
        return []

    minCX = p.bbox.minX >> 4
    maxCX = p.bbox.maxX >> 4
    minCZ = p.bbox.minZ >> 4
    maxCZ = p.bbox.maxZ >> 4

    other_pieces = gen.pieces[:-1]   # tout sauf la PortalRoom

    cands = []
    for cx in range(minCX, maxCX + 1):
        for cz in range(minCZ, maxCZ + 1):
            cb = BoundingBox(cx << 4, -32768, cz << 4,
                              (cx << 4) + 15, 32767, (cz << 4) + 15)
            is_first = True
            for op in other_pieces:
                if cb.intersects(op.bbox):
                    is_first = False
                    break
            if is_first:
                cands.append((cx, cz))
    return cands


def _find_portal_chunk(gen: StrongholdGenerator) -> tuple[int, int] | None:
    """
    Renvoie le chunk où MC place les frames du End Portal.

    Règle (déduite empiriquement contre des cas en jeu) :
      - S'il existe UN SEUL chunk où la PortalRoom est `is_first=true`
        (= chunk où aucune autre pièce du stronghold n'est présente),
        c'est lui que MC utilise.
        → Cas validé : seed 1000 → 0 yeux, seed 2040984539113960933 → 12 yeux.

      - S'il existe PLUSIEURS chunks `is_first` (cas portail à cheval entre
        chunks "vides"), on retombe sur le **chunk d'origine** = chunk
        contenant le coin (minX, minZ) de la bbox.
        → Cas validé : seed -1763418413950277470 startChunk (87,21) → 1 œil.

      - Si AUCUN chunk n'a `is_first=true` (= toute la zone autour du
        portail contient d'autres pièces), on prend l'origin chunk.

    NOTE : pour les cas "multi-chunks ambigus", la règle MC précise dépend
    de l'ordre de chargement des chunks par le joueur, donc le résultat
    n'est pas toujours déterministe. La règle ci-dessus est la meilleure
    approximation.
    """
    p = gen.portal_room_piece
    if p is None:
        return None

    cands = _find_portal_chunk_candidates(gen)
    origin_chunk = (p.bbox.minX >> 4, p.bbox.minZ >> 4)

    # Cas 1 : un seul chunk is_first → c'est le bon
    if len(cands) == 1:
        return cands[0]

    # Cas 2 ou 3 : plusieurs ou aucun → fallback origin chunk
    return origin_chunk


def _eyes_for_chunk(world_seed: int, chunk_x: int, chunk_z: int) -> tuple[int, list[bool]]:
    """Calcule (eyes, frames) pour un chunk donné (pipeline XRSR + skip)."""
    deco = _xrsr_get_decoration_seed(int(world_seed) & _U64, chunk_x, chunk_z)
    rng = Xrsr128((deco + 40019) & _U64)
    _skip_portal_room(rng)
    frames = [rng.next_float() > 0.9 for _ in range(12)]
    return sum(frames), frames


# ──────────────────────────────────────────────────────────────────────────
# Frame positions in PortalRoom local space (offW, offD)
# ──────────────────────────────────────────────────────────────────────────
#
# Ces 12 positions ont été VÉRIFIÉES en jeu (seed -1763418413950277470,
# stronghold (-720,-2032), direction=SOUTH) — l'utilisateur a fourni les 12
# coords monde des frames qu'on a converties en (offW, offD).
#
# Ordre RNG (= ordre dans lequel MC consomme les 12 nextFloat()) :
#   indices 0..2 : côté sud (offD=8), offW=4,5,6
#   indices 3..5 : côté nord (offD=12), offW=4,5,6
#   indices 6..8 : côté ouest (offW=3), offD=9,10,11
#   indices 9..11: côté est (offW=7), offD=9,10,11
#
# Cet ordre est l'hypothèse la plus naturelle (S → N → W → E, dans le sens
# de lecture). Validé contre seeds 1000 (0 yeux), 2040984539113960933 (12),
# et -1763418413950277470 (3 yeux) — TOUS les cas connus matchent.
#
_FRAME_OFFSETS_LOCAL = [
    # rng=0..2 : côté sud (offD=8)
    (4, 8), (5, 8), (6, 8),
    # rng=3..5 : côté nord (offD=12)
    (4, 12), (5, 12), (6, 12),
    # rng=6..8 : côté ouest (offW=3)
    (3, 9), (3, 10), (3, 11),
    # rng=9..11: côté est (offW=7)
    (7, 9), (7, 10), (7, 11),
]


def _frame_world_pos(piece: Piece, rng_idx: int) -> tuple[int, int]:
    """Renvoie (world_x, world_z) de la frame d'index RNG donné, en tenant
    compte de l'orientation de la pièce."""
    offW, offD = _FRAME_OFFSETS_LOCAL[rng_idx]
    return (piece.get_world_x(offW, offD),
            piece.get_world_z(offW, offD))


def _box_edges(n_x: int, n_y: int, n_z: int) -> int:
    """
    Nombre de cellules sur le BORD d'une box `n_x × n_y × n_z`.
    Une cellule (i,j,k) est sur le bord ssi i∈{0,n_y-1} OU j∈{0,n_x-1} OU k∈{0,n_z-1}.
    """
    if n_x <= 0 or n_y <= 0 or n_z <= 0:
        return 0
    total = n_x * n_y * n_z
    if n_x <= 2 or n_y <= 2 or n_z <= 2:
        return total
    interior = (n_x - 2) * (n_y - 2) * (n_z - 2)
    return total - interior


def _gen_box_selector_rng(rng: 'Xrsr128', piece: 'Piece', chunkBB: 'BoundingBox',
                           x0: int, y0: int, z0: int,
                           x1: int, y1: int, z1: int,
                           check_air: bool) -> None:
    """
    Reproduit la consommation RNG de
        generateBox(level, chunkBB, x0,y0,z0, x1,y1,z1, check_air, rng, SMOOTH_STONE_SELECTOR)
    avec coords LOCALES (x0..x1, y0..y1, z0..z1) dans la pièce.

    SmoothStoneSelector.next() consomme 1 nextFloat UNIQUEMENT sur les bords.
    Sur les cellules intérieures, il pose CAVE_AIR sans RNG.

    Si check_air=True : MC fait `if (getBlock(j,i,k, chunkBB).isAir()) continue`,
    et `getBlock` retourne AIR pour toute position monde HORS chunkBB → skip.
    Donc les cellules hors chunkBB ne consomment **PAS** de RNG si check_air=True.
    Pour les cellules DANS chunkBB, on assume que le bloc n'est pas air (terrain
    naturel) → consomme RNG.

    Si check_air=False : RNG consommé pour TOUTES les cellules-bord, peu importe
    le chunkBB.

    NOTE : MC itère dans l'ordre (i=y, j=x, k=z) → ordre RNG.
    """
    n_x = x1 - x0 + 1
    n_y = y1 - y0 + 1
    n_z = z1 - z0 + 1
    if n_x <= 0 or n_y <= 0 or n_z <= 0:
        return

    if not check_air:
        # check_air=False : tous les bords consomment, indépendant de chunkBB
        for _ in range(_box_edges(n_x, n_y, n_z)):
            rng.next_float()
        return

    # check_air=True : on doit itérer dans l'ordre exact de MC et tester
    # chunkBB.isInside(world_pos) pour chaque cellule. Si hors chunkBB → skip.
    # Si dans chunkBB ET sur le bord LOCAL de la box → 1 nextFloat.
    for i in range(y0, y1 + 1):
        for j in range(x0, x1 + 1):
            for k in range(z0, z1 + 1):
                # Position monde de cette cellule
                wx = piece.get_world_x(j, k)
                wy = piece.get_world_y(i)
                wz = piece.get_world_z(j, k)
                # check_air et hors chunkBB → skip (getBlock retourne AIR)
                if not (chunkBB.minX <= wx <= chunkBB.maxX and
                        chunkBB.minY <= wy <= chunkBB.maxY and
                        chunkBB.minZ <= wz <= chunkBB.maxZ):
                    continue
                # Dans chunkBB : test isEdge pour le selector
                is_edge = (i == y0 or i == y1 or
                           j == x0 or j == x1 or
                           k == z0 or k == z1)
                # blockSelector.next consomme 1 nextFloat SI isEdge (sinon CAVE_AIR sans RNG)
                if is_edge:
                    rng.next_float()


def _gen_maybe_box_rng(rng: 'Xrsr128',
                        x0: int, y0: int, z0: int,
                        x1: int, y1: int, z1: int) -> None:
    """
    Reproduit la consommation RNG de generateMaybeBox(rng, f, x0..x1, y0..y1, z0..z1, ...).
    Dans MC : pour CHAQUE cellule, on appelle `randomSource.nextFloat() > f`
    AVANT le check de placement. → 1 nextFloat par cellule.
    """
    n_x = x1 - x0 + 1
    n_y = y1 - y0 + 1
    n_z = z1 - z0 + 1
    if n_x > 0 and n_y > 0 and n_z > 0:
        for _ in range(n_x * n_y * n_z):
            rng.next_float()


def _maybe_block_rng(rng: 'Xrsr128') -> None:
    """maybeGenerateBlock : 1 nextFloat toujours."""
    rng.next_float()


def _create_chest_rng(rng: 'Xrsr128', piece: Piece, chunkBB: BoundingBox,
                       lx: int, ly: int, lz: int) -> None:
    """
    createChest consomme 1 nextLong UNIQUEMENT si la position monde est dans
    chunkBB (cf StructurePiece.createChest source MC).
    """
    wx = piece.get_world_x(lx, lz)
    wy = piece.get_world_y(ly)
    wz = piece.get_world_z(lx, lz)
    if (chunkBB.minX <= wx <= chunkBB.maxX and
        chunkBB.minY <= wy <= chunkBB.maxY and
        chunkBB.minZ <= wz <= chunkBB.maxZ):
        rng.next_long()


def _simulate_piece_postprocess(rng: 'Xrsr128', piece: Piece,
                                  chunkBB: BoundingBox) -> None:
    """
    Simule la consommation RNG de `piece.postProcess(...)` pour chaque type
    de pièce stronghold, EN ACCORD avec le source MC 1.19.4-1.21.4.

    Pour chaque pièce, le PREMIER generateBox utilise `checkAir=true` (qui
    skipe les cellules hors chunkBB), les suivants `checkAir=false` (consomment
    tous les bords). Cf source MC StrongholdPieces.java.
    """
    t = piece.type

    if t == STRAIGHT:
        # generateBox(0,0,0, 4,4,6, true, rng, SELECTOR)  → 5×5×7  checkAir=TRUE
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 4, 4, 6, True)
        # 4× maybeGenerateBlock (torches) — toujours consommées (1 nextFloat chaque)
        _maybe_block_rng(rng); _maybe_block_rng(rng)
        _maybe_block_rng(rng); _maybe_block_rng(rng)
        # generateBox CAVE_AIR : pas de RNG
        return

    if t == PRISON_HALL:
        # generateBox(0,0,0, 8,4,10, true, rng, SELECTOR)  checkAir=TRUE
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 8, 4, 10, True)
        # generateBox(4,1,1, 4,3,1, false, rng, SELECTOR)  checkAir=FALSE
        _gen_box_selector_rng(rng, piece, chunkBB, 4, 1, 1, 4, 3, 1, False)
        _gen_box_selector_rng(rng, piece, chunkBB, 4, 1, 3, 4, 3, 3, False)
        _gen_box_selector_rng(rng, piece, chunkBB, 4, 1, 7, 4, 3, 7, False)
        _gen_box_selector_rng(rng, piece, chunkBB, 4, 1, 9, 4, 3, 9, False)
        return

    if t == LEFT_TURN or t == RIGHT_TURN:
        # generateBox(0,0,0, 4,4,4, true, rng, SELECTOR)  checkAir=TRUE
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 4, 4, 4, True)
        return

    if t == ROOM_CROSSING:
        # generateBox(0,0,0, 10,6,10, true, rng, SELECTOR)  checkAir=TRUE
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 10, 6, 10, True)
        # additionalData stocke le type de room (0..4)
        ctype = piece.additionalData
        if ctype == 2:
            _create_chest_rng(rng, piece, chunkBB, 3, 4, 8)
        return

    if t == STRAIGHT_STAIRS_DOWN:
        # generateBox(0,0,0, 4,10,7, true, rng, SELECTOR)
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 4, 10, 7, True)
        return

    if t == STAIRS_DOWN:
        # generateBox(0,0,0, 4,10,4, true, rng, SELECTOR)
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 4, 10, 4, True)
        return

    if t == FIVE_CROSSING:
        # generateBox(0,0,0, 9,8,10, true, rng, SELECTOR)  checkAir=TRUE
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 9, 8, 10, True)
        # generateBox SMOOTH_STONE (sub-rooms) : checkAir=FALSE
        _gen_box_selector_rng(rng, piece, chunkBB, 1, 2, 1, 8, 2, 6, False)
        _gen_box_selector_rng(rng, piece, chunkBB, 4, 1, 5, 4, 4, 9, False)
        _gen_box_selector_rng(rng, piece, chunkBB, 8, 1, 5, 8, 4, 9, False)
        _gen_box_selector_rng(rng, piece, chunkBB, 1, 4, 7, 3, 4, 9, False)
        _gen_box_selector_rng(rng, piece, chunkBB, 1, 3, 5, 3, 3, 6, False)
        _gen_box_selector_rng(rng, piece, chunkBB, 5, 1, 7, 7, 1, 8, False)
        return

    if t == CHEST_CORRIDOR:
        # generateBox(0,0,0, 4,4,6, true, rng, SELECTOR)
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 4, 4, 6, True)
        # createChest at (3, 2, 3)
        _create_chest_rng(rng, piece, chunkBB, 3, 2, 3)
        return

    if t == LIBRARY:
        is_tall = (piece.bbox.maxY - piece.bbox.minY) > 6
        n2 = 11 if is_tall else 6
        # generateBox(0,0,0, 13, n2-1, 14, true, rng, SELECTOR)
        _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 13, n2 - 1, 14, True)
        # generateMaybeBox(rng, 0.07, 2,1,1, 11,4,13, ...)
        _gen_maybe_box_rng(rng, 2, 1, 1, 11, 4, 13)
        # 2× createChest (la 2e seulement si isTall)
        _create_chest_rng(rng, piece, chunkBB, 3, 3, 5)
        if is_tall:
            _create_chest_rng(rng, piece, chunkBB, 12, 8, 1)
        return

    if t == FILLER_CORRIDOR:
        # FillerCorridor.postProcess place uniquement des blocs constants → 0 RNG
        return

    # PORTAL_ROOM est traité spécialement (par le caller)
    return


def _simulate_portal_room_prefix(rng: 'Xrsr128', piece: Piece,
                                   chunkBB: BoundingBox) -> None:
    """
    Reproduit la consommation RNG de PortalRoom.postProcess() AVANT la
    boucle des 12 frames.

    Le 1er generateBox a checkAir=FALSE donc TOUS les bords consomment, peu
    importe le chunkBB. (Cf source : `false` dans `generateBox(...,false,...)`.)
    """
    # Note: TOUS les generateBox de PortalRoom sont avec checkAir=false
    _gen_box_selector_rng(rng, piece, chunkBB, 0, 0, 0, 10, 7, 15, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 1, 6, 1, 1, 6, 14, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 9, 6, 1, 9, 6, 14, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 2, 6, 1, 8, 6, 2, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 2, 6, 14, 8, 6, 14, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 1, 1, 1, 2, 1, 4, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 8, 1, 1, 9, 1, 4, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 3, 1, 8, 7, 1, 12, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 4, 1, 5, 6, 1, 7, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 4, 2, 6, 6, 2, 7, False)
    _gen_box_selector_rng(rng, piece, chunkBB, 4, 3, 7, 6, 3, 7, False)


def _lcg_get_decoration_seed(world_seed: int, chunk_x: int, chunk_z: int) -> int:
    """WorldgenRandom.setDecorationSeed (LCG, MC 1.13-1.17). Les deux termes sont
    ADDITIONNES puis ^ world_seed -- different de setLargeFeatureSeed (qui XOR les
    termes, utilise pour la GENERATION des pieces, pas le decoseed des yeux).
        setSeed(ws); a = nextLong()|1; b = nextLong()|1;
        l = (cx*16)*a + (cz*16)*b ^ ws
    """
    ws = int(world_seed) & _U64
    r = JavaRandom(world_seed)
    a = r.nextLong() | 1
    b = r.nextLong() | 1
    bx = chunk_x << 4
    bz = chunk_z << 4
    term = ((bx * a) + (bz * b)) & _U64
    return _to_int64((term ^ ws) & _U64)


# End-portal "eye" RNG = WorldgenRandom.setFeatureSeed(decorationSeed, index, step)
#                       = decorationSeed + index + 10000 * step
# where (step, index) is the stronghold's slot in the per-step, registry-ordered
# structure list, and decorationSeed = setDecorationSeed(worldSeed, cx*16, cz*16).
# Two things change across versions: the decoration RNG family, and (step, index).
#   1.13-1.17 (mc < 22): LCG (LegacyRandomSource). OLD structure system, stronghold
#       step = STRONGHOLDS (ordinal 5), index 0  ->  +50000.
#   1.18.0/1.18.1 (mc == 22): Xoroshiro128++. STILL the old structure system
#       (data-driven structures only arrived in 1.18.2), so stronghold step =
#       STRONGHOLDS (5), index 0  ->  +50000. (Verified vs the 1.18.1 checker / in
#       game: seed 1000 strongholds -> 0,0,2 eyes.)
#   1.19+ (mc >= 24): Xoroshiro128++. Data-driven structures; stronghold step =
#       SURFACE_STRUCTURES (ordinal 4 — verified in 1.19.4/1.21.4 source:
#       Structures.structure(tag, BURY) defaults to SURFACE_STRUCTURES). index =
#       its registry slot among SURFACE_STRUCTURES structures (per-version).
# The index is the stronghold's rank among the SURFACE_STRUCTURES structures in
# REGISTRY order:
#   1.19.0-1.19.2: registry = code registration order (Structures bootstrap):
#       pillager_outpost, [mineshafts: underground], mansion, jungle_temple,
#       desert_pyramid, igloo, shipwreck, shipwreck_beached, swamp_hut,
#       STRONGHOLD  -> index 8.
#   1.19.3+: worldgen registries are loaded from the vanilla data pack, sorted
#       by id (alphabetical): bastion_remnant … shipwreck_beached, STRONGHOLD
#       -> index 19 (in-game verified in 1.21.x).
_EYE_SURFACE_INDEX = {   # mc -> stronghold index within SURFACE_STRUCTURES (1.19+)
    23: 8,    # 1.19.0-1.19.2 (registration order)
    24: 19,   # 1.19.3/1.19.4 (alphabetical)
    25: 19,   # 1.20.x   (UNVERIFIED)
    26: 19,   # 1.21.1   (UNVERIFIED)
    28: 19,   # 1.21.4   (UNVERIFIED)
}


def _eye_feature_offset(mc_version: int) -> int:
    """Offset added to the (Xoroshiro) decoration seed for the stronghold's eye
    RNG, 1.18+. mc 22 = 1.18.0/1.18.1 still uses the STRONGHOLDS step (+50000);
    1.19+ uses SURFACE_STRUCTURES (+40000 + registry index)."""
    if mc_version <= 22:               # 1.18.0/1.18.1: STRONGHOLDS step (5), idx 0
        return 50000
    return 40000 + _EYE_SURFACE_INDEX.get(mc_version, 19)


EYE_OFFSET_LEGACY = 40019             # hard-wired in the native 3-argument exports


def _eye_rng_for_chunk(world_seed: int, chunk_x: int, chunk_z: int,
                       mc_version: int = 28):
    """Position-seeded decoration RNG used to read the 12 end-portal frame floats,
    routed by version (LCG <1.18, Xoroshiro 1.18+; offset via _eye_feature_offset)."""
    if mc_version < 22:
        deco = _lcg_get_decoration_seed(world_seed, chunk_x, chunk_z)
        return JavaRandom((deco + 50000) & _U64)
    deco = _xrsr_get_decoration_seed(int(world_seed) & _U64, chunk_x, chunk_z)
    return Xrsr128((deco + _eye_feature_offset(mc_version)) & _U64)


def _frames_for_chunk(world_seed: int, gen: StrongholdGenerator,
                      chunk_x: int, chunk_z: int,
                      mc_version: int = 28) -> tuple:
    """Simule le postProcess des pieces qui intersectent (chunk_x,chunk_z) en ordre
    BFS jusqu'a la PortalRoom, puis lit 12 nextFloat. Marche pour 1.16 ET 1.18+
    (RNG route par _eye_rng_for_chunk). Retourne (eyes, frames). Remplace le chemin
    _eyes_for_chunk (matrice skip xoroshiro) qui n'existe pas en LCG."""
    p = gen.portal_room_piece
    if p is None:
        return 0, [False] * 12
    rng = _eye_rng_for_chunk(world_seed, chunk_x, chunk_z, mc_version)
    chunkBB = BoundingBox(chunk_x << 4, -32768, chunk_z << 4,
                          (chunk_x << 4) + 15, 32767, (chunk_z << 4) + 15)
    for piece in gen.pieces:
        if not piece.bbox.intersects(chunkBB):
            continue
        if piece is p:
            _simulate_portal_room_prefix(rng, piece, chunkBB)
            break
        _simulate_piece_postprocess(rng, piece, chunkBB)
    frames = [rng.next_float() > 0.9 for _ in range(12)]
    return sum(frames), frames


def _eyes_per_frame(world_seed: int, gen: StrongholdGenerator, mc_version: int = 28) -> list[bool]:
    """
    Calcule les 12 frames du portail en simulant exactement le pipeline MC.

    Pour CHAQUE chunk où la PortalRoom est physiquement (1, 2 ou 4 chunks) :
      1. l = setDecorationSeed(worldSeed, chunkX*16, chunkZ*16)
      2. setFeatureSeed(l, 0, 5) = setSeed(l + 50000)  (step STRONGHOLDS=5)
      3. Pour chaque pièce du stronghold (en ordre BFS) qui intersecte le
         chunkBox : simuler sa consommation RNG via _simulate_piece_postprocess
      4. Quand on arrive à PortalRoom : simuler son préfixe via
         _simulate_portal_room_prefix
      5. Lire 12 nextFloat → eyes[]

    Pour chaque frame physique : prend la valeur du chunk qui contient sa
    position monde (les autres chunks essaient aussi de placer mais MC filtre
    par chunkBB.isInside(worldPos)).

    Reproduit le source MC StructureStart.placeInChunk + postProcess de chaque
    pièce.
    """
    p = gen.portal_room_piece
    if p is None:
        return [False] * 12

    chunk_cache: dict = {}

    def get_chunk_frames(cx: int, cz: int) -> list[bool]:
        if (cx, cz) in chunk_cache:
            return chunk_cache[(cx, cz)]
        # 1) decoration_seed pour ce chunk
        rng = _eye_rng_for_chunk(world_seed, cx, cz, mc_version)
        # 3) chunkBB du chunk en cours
        chunkBB = BoundingBox(cx << 4, -32768, cz << 4,
                                (cx << 4) + 15, 32767, (cz << 4) + 15)
        # 4) Simuler chaque pièce qui intersecte ce chunk, en ordre BFS,
        #    jusqu'à arriver à PortalRoom
        for piece in gen.pieces:
            if not piece.bbox.intersects(chunkBB):
                continue
            if piece is p:
                # PortalRoom : simuler son préfixe puis sortir
                _simulate_portal_room_prefix(rng, piece, chunkBB)
                break
            # Autre pièce avant PortalRoom : simuler sa consommation RNG
            _simulate_piece_postprocess(rng, piece, chunkBB)
        # 5) Lire 12 nextFloat → eyes[]
        frames = [rng.next_float() > 0.9 for _ in range(12)]
        chunk_cache[(cx, cz)] = frames
        return frames

    # Pour chaque frame physique, lire la valeur du chunk où elle se trouve
    result = []
    frame_chunks = set()
    for rng_idx in range(12):
        wx, wz = _frame_world_pos(p, rng_idx)
        frame_cx, frame_cz = wx >> 4, wz >> 4
        frame_chunks.add((frame_cx, frame_cz))
        result.append(get_chunk_frames(frame_cx, frame_cz)[rng_idx])

    # 12-eye ACTIVATION rule (verified in game, all versions): each chunk that
    # touches the portal evaluates all 12 frames with its OWN decoration RNG. If
    # ANY of those chunks yields 12 eyes on its own — even if it doesn't cover the
    # whole portal room — the END portal lights up as a full 12/12. So when the
    # portal spans several chunks, a seed whose real per-frame "mix" is < 12 still
    # counts as 12 if some single touched chunk gives 12. (12-eye case only;
    # otherwise we keep the exact per-frame mix.)
    if len(frame_chunks) > 1:
        for (cx, cz) in frame_chunks:
            if all(get_chunk_frames(cx, cz)):
                return [True] * 12
    return result


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def count_stronghold_frames(world_seed: int, bx: int, bz: int,
                            mc_version: int = 28) -> list[bool]:
    """
    Retourne la liste de 12 frames du End Portal.

    Pipeline (logique chunk-par-frame, validée empiriquement) :
      1. BFS LCG → trouve la PORTAL_ROOM
      2. Pour chaque frame physique (12 positions autour du carré 3x3 du
         portail), on calcule sa position monde, on identifie son chunk,
         et on consomme le RNG de CE chunk-là à l'index correspondant.
      3. C'est cette valeur qui est écrite dans le monde par MC (chaque
         chunk fait postProcess avec son propre decoration_seed et n'écrit
         physiquement que les frames qui sont DANS son chunkBox).

    Validé contre :
      - seed 1000 → 0 yeux ✓
      - seed 2040984539113960933 → 12 yeux ✓
      - seed -1763418413950277470 cas (87,21) → 1 oeil ✓
      - seed -1763418413950277470 cas (-45,-127) → 3 yeux ✓ (positions
        exactes vérifiées en jeu : (-690,-1986), (-692,-1982), (-693,-1983))
    """
    cx, cz = int(bx) >> 4, int(bz) >> 4
    gen = StrongholdGenerator()
    gen.pre118 = _use_old_filler(mc_version)
    gen.generate(int(world_seed), cx, cz)
    if gen.portal_room_piece is None:
        return [False] * 12

    return _eyes_per_frame(int(world_seed), gen, mc_version)


def count_stronghold_eyes(world_seed: int, bx: int, bz: int,
                          mc_version: int = 28) -> int:
    """Retourne le nombre d'yeux dans le portail (0..12)."""
    return sum(count_stronghold_frames(world_seed, bx, bz, mc_version))


def _generate_full_bfs(world_seed: int, chunk_x: int, chunk_z: int,
                       pre118: bool = False):
    """
    Genere le stronghold en reproduisant le BFS COMPLET de Minecraft (vide
    toute la file `pending`), SANS l'arret anticipe a la PortalRoom utilise
    par Gaider/12-eye. Indispensable pour obtenir l'etat RNG exact au moment
    de moveBelowSeaLevel, car MC genere tout le donjon AVANT de decaler.

    Retourne (gen, rng) ou rng est dans l'etat exact d'avant moveBelowSeaLevel.

    Valide in-game (seed 8799660861697605236) :
      (1428,916) frames Y=-48 ; (-1788,804) frames Y=-19 ; (324,-2508) Y=+6
    et seed -7837379773292899934 (-2572,-412) frames Y=29.
    """
    # Generateur qui NE stoppe PAS a la PortalRoom (contrairement a 12-eye).
    # Le vrai MC traite la PortalRoom comme une piece normale : elle enregistre
    # juste le pointeur, et la generation continue jusqu'a epuisement naturel
    # (update_piece_weight met gen_stopped=True quand tous les types
    # obligatoires sont places — exactement comme StrongholdPieces de MC).
    class _GenFull(StrongholdGenerator):
        def create_piece(self, pt, rng_, direction_, depth_, bb_):
            if pt == PORTAL_ROOM:
                piece = Piece(pt, depth_, bb_, direction_, 0)
                if self.portal_room_piece is None:
                    self.portal_room_piece = piece
                # PAS de gen_stopped=True ici : le donjon continue de generer.
                return piece
            return StrongholdGenerator.create_piece(self, pt, rng_,
                                                     direction_, depth_, bb_)

    gen = _GenFull()
    gen.pre118 = pre118
    attempt = 0
    rng = JavaRandom(0)
    while True:
        gen.reset_pieces()
        rng.setLargeFeatureSeed(_to_int64((world_seed + attempt) & _U64),
                                 chunk_x, chunk_z)
        attempt += 1
        x = (chunk_x << 4) + 2
        z = (chunk_z << 4) + 2
        gen.startX = x
        gen.startZ = z
        direction = HORIZONTAL[rng.nextInt_bound(4)]
        bbox = make_bbox_for_start(x, 64, z, direction, 5, 11, 5)
        start_piece = Piece(STAIRS_DOWN, 0, bbox, direction, 1)
        gen.pieces.append(start_piece)
        gen.start_direction = direction
        gen.add_children(start_piece, rng)
        # BFS COMPLET (MC : while !list.isEmpty()). gen_stopped sera mis a True
        # naturellement par update_piece_weight (pas par la PortalRoom). Tant
        # que la file n'est pas vide, chaque tour consomme nextInt(len(pending)).
        while gen.pending:
            i = rng.nextInt_bound(len(gen.pending))
            idx = gen.pending.pop(i)
            gen.add_children(gen.pieces[idx], rng)
        if gen.portal_room_piece is not None:
            gen.last_attempt = attempt
            return gen, rng


def _move_below_sea_level(gen, rng, sea_level: int = 63,
                           min_y: int = -64, margin: int = 10,
                           inflate: int = 0) -> int:
    """
    Reproduit StructurePiecesBuilder.moveBelowSeaLevel — l'etape finale de
    generation qui decale verticalement TOUT le donjon. Retourne l'offset n6.

      n4 = seaLevel - margin
      bb = bounding box GLOBALE de toutes les pieces
      n5 = bb.getYSpan() + minY + 1     (getYSpan = maxY-minY+1)
      si n5 < n4: n5 += rng.nextInt(n4 - n5)
      n6 = n5 - bb.maxY
    """
    # 1.17.x : NoiseAffectingStructureStart gonfle la BB de `inflate` (=12) sur
    # chaque face AVANT moveBelowSeaLevel -> +12 au sommet. Les VRAIES pieces ne
    # sont pas gonflees ; seul le calcul de l'offset utilise la BB gonflee.
    gMinY = min(p.bbox.minY for p in gen.pieces) - inflate
    gMaxY = max(p.bbox.maxY for p in gen.pieces) + inflate
    yspan = gMaxY - gMinY + 1
    n4 = sea_level - margin
    n5 = yspan + min_y + 1
    if n5 < n4:
        n5 += rng.nextInt_bound(n4 - n5)
    return n5 - gMaxY


def _sea_min_for_mc(mc_version: int) -> tuple:
    # Overworld 1.18+ : sea=63, minY=-64 (deepslate world expansion, since 1.18 =
    # mc 22). 1.9-1.17 : world 0..255 (minY=0). NOTE: must be >=22, not >=28 —
    # 1.18/1.19/1.20/1.21.1 (mc 22/24/25/26) ALSO have minY=-64; the old >=28
    # made their stronghold/frame Y ~64 too high (verified vs the 1.18.1 checker).
    if mc_version >= 22:
        return (63, -64)
    return (63, 0)


def _use_old_filler(mc_version: int) -> bool:
    """FillerCorridor : ANCIEN (boucle 3..1, test i-1, retour i) pour <= 1.16
    (mc <= 20 = MC_1_16) ; NOUVEAU (boucle 2..1, test i, retour i+1) des 1.17
    (mc >= 21). Le changement de structure date de 1.17, PAS de 1.18."""
    return mc_version <= 20


def _inflate_for_mc(mc_version: int) -> int:
    """1.17.x UNIQUEMENT : NoiseAffectingStructureStart gonfle la bounding box
    de 12 avant moveBelowSeaLevel -> strongholds +12 blocs. Ne en 1.17,
    supprime en 1.18 (StructurePiecesBuilder.getBoundingBox = BB reelle).
    mc == 21 = MC_1_17_1."""
    return 12 if mc_version == 21 else 0


def get_stronghold_portal_full(world_seed: int, bx: int, bz: int,
                                mc_version: int = 28):
    """
    Calcule la position EXACTE du end portal apres le decalage vertical de MC.

    Retourne un dict : center_x, center_z (centre exact du portail, bloc
    END_PORTAL central local (5,3,10)), frame_y (Y EXACT des end portal
    frames, valide in-game), lowest_y / highest_y (extremes du donjon),
    offset (decalage n6). Ou None si pas de portal room.
    """
    cx, cz = int(bx) >> 4, int(bz) >> 4
    # 1.13-1.17 (mc < 22 = MC_1_18_2) utilise le FillerCorridor pre-1.18.
    gen, rng = _generate_full_bfs(int(world_seed), cx, cz,
                                  pre118=_use_old_filler(mc_version))
    pr = gen.portal_room_piece
    if pr is None:
        return None
    sea, miny = _sea_min_for_mc(mc_version)
    n6 = _move_below_sea_level(gen, rng, sea, miny, 10,
                               _inflate_for_mc(mc_version))
    gMinY = min(p.bbox.minY for p in gen.pieces)
    gMaxY = max(p.bbox.maxY for p in gen.pieces)
    return {
        "center_x":  pr.get_world_x(5, 10),
        "center_z":  pr.get_world_z(5, 10),
        "frame_y":   pr.bbox.minY + 3 + n6,
        "lowest_y":  gMinY + n6,
        "highest_y": gMaxY + n6,
        "offset":    n6,
    }


# Positions LOCALES des 12 END_PORTAL_FRAME dans la PortalRoom (source MC), y=3.
_PORTAL_FRAME_LOCALS = [(4, 8), (5, 8), (6, 8), (4, 12), (5, 12), (6, 12),
                        (3, 9), (3, 10), (3, 11), (7, 9), (7, 10), (7, 11)]


def get_end_portal_frames(world_seed: int, bx: int, bz: int,
                          mc_version: int = 28):
    """Retourne (frames, center_x, center_z, frame_y) ou None.
    frames = liste des 12 positions monde (x, y, z) des END_PORTAL_FRAME."""
    cx, cz = int(bx) >> 4, int(bz) >> 4
    gen, rng = _generate_full_bfs(int(world_seed), cx, cz,
                                  pre118=_use_old_filler(mc_version))
    pr = gen.portal_room_piece
    if pr is None:
        return None
    sea, miny = _sea_min_for_mc(mc_version)
    n6 = _move_below_sea_level(gen, rng, sea, miny, 10,
                               _inflate_for_mc(mc_version))
    fy = pr.bbox.minY + 3 + n6
    frames = [(pr.get_world_x(fx, fz), fy, pr.get_world_z(fx, fz))
              for fx, fz in _PORTAL_FRAME_LOCALS]
    return frames, pr.get_world_x(5, 10), pr.get_world_z(5, 10), fy


def get_stronghold_eyes_and_maxy(world_seed: int, bx: int, bz: int,
                                 mc_version: int = 28):
    """Retourne (eyes, highest_y) en UNE seule generation full-BFS.
    highest_y = point le plus haut du donjon apres moveBelowSeaLevel (= ce que
    get_stronghold_portal_full appelle "highest_y"). Perf : 1 generation par
    stronghold au lieu de 2 (count_stronghold_eyes + get_stronghold_portal_full).
    Les yeux sont calcules via _eyes_per_frame sur le gen full-BFS : identique au
    gen "stop a la PortalRoom" car _eyes_per_frame s'arrete a la PortalRoom.
    Routage version : pre118=(mc<22), RNG des yeux route par _eye_rng_for_chunk.
    """
    cx, cz = int(bx) >> 4, int(bz) >> 4
    gen, rng = _generate_full_bfs(int(world_seed), cx, cz,
                                  pre118=_use_old_filler(mc_version))
    if gen.portal_room_piece is None:
        return 0, None
    sea, miny = _sea_min_for_mc(mc_version)
    n6 = _move_below_sea_level(gen, rng, sea, miny, 10,
                               _inflate_for_mc(mc_version))
    gMaxY = max(p.bbox.maxY for p in gen.pieces)
    eyes = sum(_eyes_per_frame(int(world_seed), gen, mc_version))
    return eyes, gMaxY + n6


def get_stronghold_portal_pos(world_seed: int, bx: int, bz: int,
                               mc_version: int = 28) -> tuple:
    """
    Retourne le centre EXACT du end portal (x, y, z), avec le Y reel des end
    portal frames (apres moveBelowSeaLevel). Pour un /tp precis.
    """
    info = get_stronghold_portal_full(world_seed, bx, bz, mc_version)
    if info is None:
        return (int(bx), 64, int(bz))
    return (info["center_x"], info["frame_y"], info["center_z"])


# ──────────────────────────────────────────────────────────────────────────
# Diagnostics — fonction one-shot qui fait toute la simulation
# ──────────────────────────────────────────────────────────────────────────

_DIRECTION_NAMES = {NORTH: "NORTH", SOUTH: "SOUTH", WEST: "WEST", EAST: "EAST"}


def get_stronghold_diagnostics(world_seed: int, bx: int, bz: int,
                                 mc_version: int = 28) -> dict:
    """
    Effectue une simulation complète du stronghold à (bx, bz) et retourne
    un dict avec toutes les infos utiles pour l'UI : position, frames, eyes,
    chunk de la portal room, direction, nombre de pièces, attempt count.

    Cette fonction fait UNE SEULE simulation (économie vs appeler
    count_stronghold_eyes + count_stronghold_frames + get_stronghold_portal_pos
    séparément qui régénèrent le stronghold à chaque appel).

    Retourne :
        {
            "eyes":              int (0..12),
            "frames":            list[bool] de 12,
            "portal_pos":        (x, y, z) — centre approximatif du portail,
            "chunk_start":       (cx, cz) — chunk du startPiece,
            "portal_room_chunk": (pcx, pcz) ou None,
            "direction":         str — orientation du startPiece ("NORTH" etc.),
            "n_pieces":          int — nombre de pièces générées,
            "attempt":           int — numéro de l'attempt qui a réussi,
        }
    """
    cx, cz = int(bx) >> 4, int(bz) >> 4

    gen = StrongholdGenerator()
    gen.pre118 = _use_old_filler(mc_version)
    gen.generate(int(world_seed), cx, cz)

    # Default empty result si pas de portal trouvé
    if gen.portal_room_piece is None:
        return {
            "eyes": 0,
            "frames": [False] * 12,
            "portal_pos": (int(bx), 64, int(bz)),
            "chunk_start": (cx, cz),
            "portal_room_chunk": None,
            "direction": _DIRECTION_NAMES.get(getattr(gen, "start_direction", -1),
                                                "?"),
            "n_pieces": len(gen.pieces),
            "attempt": getattr(gen, "last_attempt", 0),
        }

    p = gen.portal_room_piece
    portal_pos = (
        p.get_world_x(5, 9),
        p.bbox.minY + 3,   # niveau du portail interior
        p.get_world_z(5, 9),
    )

    # Récupère TOUS les chunks candidats (cas portail entre plusieurs chunks)
    candidates = _find_portal_chunk_candidates(gen)
    pchunk = _find_portal_chunk(gen)

    # Calcule eyes pour chaque candidat
    candidate_results = []
    for c in candidates:
        eyes_c, frames_c = _frames_for_chunk(int(world_seed), gen, c[0], c[1], mc_version)
        candidate_results.append({
            "chunk": c,
            "eyes":  eyes_c,
            "frames": frames_c,
        })

    # Frames "principales" : logique chunk-par-frame (validée).
    frames = _eyes_per_frame(int(world_seed), gen, mc_version)
    eyes = sum(frames)

    # Détecte ambiguïté : on regarde si les chunks `is_first` candidats
    # auraient donné des comptes différents (indice qu'on est dans un cas
    # multi-chunks où le résultat est sensible à la logique de sélection).
    distinct_eye_counts = sorted({r["eyes"] for r in candidate_results})

    # Compte les chunks distincts qui contiennent au moins une des 12 frames
    # physiques. Ça donne une mesure précise de "à quel point le portail est
    # à cheval entre plusieurs chunks" (1 = tout dans un chunk, 2 = 2 chunks,
    # jusqu'à 4 max si exactement au milieu).
    frame_chunks = set()
    for rng_idx in range(12):
        wx, wz = _frame_world_pos(p, rng_idx)
        frame_chunks.add((wx >> 4, wz >> 4))
    n_chunks_spanned = len(frame_chunks)

    multi_chunk = n_chunks_spanned > 1
    ambiguous = multi_chunk and len(distinct_eye_counts) > 1

    # ── Y EXACT du portail via moveBelowSeaLevel (validé in-game) ──────────
    # Centre exact du portail + Y des end portal frames + extrêmes du donjon.
    pf = get_stronghold_portal_full(int(world_seed), int(bx), int(bz),
                                     mc_version)
    if pf is not None:
        portal_pos = (pf["center_x"], pf["frame_y"], pf["center_z"])
        portal_extra = {
            "frame_y":    pf["frame_y"],
            "lowest_y":   pf["lowest_y"],
            "highest_y":  pf["highest_y"],
            "center_x":   pf["center_x"],
            "center_z":   pf["center_z"],
        }
    else:
        portal_extra = {}

    return {
        "eyes": eyes,
        "frames": frames,
        "portal_pos": portal_pos,
        "chunk_start": (cx, cz),
        "portal_room_chunk": pchunk,
        "direction": _DIRECTION_NAMES.get(getattr(gen, "start_direction", -1),
                                            "?"),
        "n_pieces": len(gen.pieces),
        "attempt": getattr(gen, "last_attempt", 0),
        "n_chunks_spanned": n_chunks_spanned,
        # Infos d'ambiguïté (gardées pour debug, plus affichées)
        "candidates": candidate_results,
        "ambiguous":  ambiguous,
        "eyes_range": (min(distinct_eye_counts), max(distinct_eye_counts))
                      if distinct_eye_counts else (0, 0),
        **portal_extra,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Seed Search API
# ─────────────────────────────────────────────────────────────────────────────

import math as _math


def get_first_stronghold_pos(world_seed: int) -> tuple:
    """
    Retourne (bx, bz) du premier stronghold pour cette world_seed.
    Port exact de StrongholdGenerator::getFirstPos (Gaider10/12-eye).
    """
    rng = JavaRandom(world_seed)
    angle    = rng.nextDouble() * _math.pi * 2.0
    distance = (4 * 32) + (rng.nextDouble() - 0.5) * (32 * 2.5)
    chunk_x  = int(round(_math.cos(angle) * distance))
    chunk_z  = int(round(_math.sin(angle) * distance))
    rng.setSeed(rng.nextLong())

    biome_cx = ((chunk_x << 4) + 8) >> 2
    biome_cz = ((chunk_z << 4) + 8) >> 2
    biome_r  = 112 >> 2   # 28

    best_x = chunk_x
    best_z = chunk_z
    m = 0
    for dz in range(-biome_r, biome_r + 1):
        bz2 = biome_cz + dz
        for dx in range(-biome_r, biome_r + 1):
            bx2 = biome_cx + dx
            if m == 0 or rng.nextInt_bound(m + 1) == 0:
                best_x = bx2 >> 2
                best_z = bz2 >> 2
            m += 1

    return (best_x << 4, best_z << 4)


def _seed_worker_process(args):
    """
    Wrapper qui délègue à `search_worker.search_chunk` (module isolé).
    Gardé pour compatibilité ascendante du module.
    """
    from search_worker import search_chunk
    return search_chunk(args)


def _run_crunch_search(seed_start, seed_count,
                        callback, stop_event, progress_callback,
                        crunch_threshold=0.035,
                        min_eyes=0, mc_version=28):
    """
    Crunchiness-only GPU search path (mushroom island pre-filter).

    Scans world seeds in batches using gaider_cuda.crunchiness_filter()
    (the yo-score filter ported from COMMISSION's KernelFilterSeeds).
    Passing seeds are then verified with the full CPU cubiomes pipeline.

    This function is called from _gpu_search_loop when use_crunch_prefilter
    is True.  It does NOT run the Gaider stronghold scan — it is designed for
    mushroom island seed hunts where stronghold position is irrelevant.

    callback(seed, eyes, bx, bz) is called for every confirmed hit.
    eyes=0, bx=0, bz=0 are passed since there is no stronghold context here;
    the caller (gui/criteria) can ignore these if using the crunch path purely
    for biome pre-filtering.
    """
    import gaider_cuda

    BATCH = 1 << 22          # ~4 million seeds per GPU batch (fast, low VRAM)
    MAX_RESULTS_PER_BATCH = 65536  # ample for threshold 0.035 (~1/9400)

    total = seed_count if seed_count > 0 else (1 << 48)
    done = 0

    while done < total:
        if stop_event is not None and stop_event.is_set():
            break

        n = min(BATCH, total - done)
        ss = seed_start + done

        try:
            total_hits, seeds = gaider_cuda.crunchiness_filter(
                seed_start=ss,
                n=n,
                threshold=crunch_threshold,
                max_results=MAX_RESULTS_PER_BATCH,
            )
        except Exception as e:
            raise RuntimeError(f"gaider_cuda.crunchiness_filter failed: {e}") from e

        # CPU verification: each seed that passed the yo-score is now checked
        # for actual mushroom island biome presence via cubiomes.
        # We use a lightweight Monte-Carlo scan at scale=64 (very fast, ~0.3ms
        # per seed) to confirm at least one mushroom cell exists within
        # SEARCH_RADIUS blocks before calling back.  This eliminates false
        # positives so the callback only fires on confirmed candidates.
        SEARCH_RADIUS = 12000   # blocks — covers most reachable islands
        MUSH_IDS = frozenset((14, 15))  # MUSHROOM_FIELDS=14, SHORE=15 in cubiomes

        from cubiomes_wrap import get_lib as _get_lib
        _lib = _get_lib()

        for _seed in seeds:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                _lib.setup_and_seed(mc_version, _seed)
                # Quick coarse scan: 17×17 grid at scale=64 (~1088-block step)
                # covers ±9000 blocks without being expensive.
                _step = 1088
                _found = False
                _bx, _bz = 0, 0
                for _gx in range(-8, 9):
                    if _found:
                        break
                    for _gz in range(-8, 9):
                        _wx = _gx * _step
                        _wz = _gz * _step
                        try:
                            _bid = _lib.get_biome_at(_wx, _wz, scale=64)
                        except Exception:
                            continue
                        if _bid in MUSH_IDS:
                            _found = True
                            _bx, _bz = _wx, _wz
                            break
                if _found:
                    callback(_seed, 0, _bx, _bz, None)
            except Exception:
                # If cubiomes fails for this seed just skip it
                pass

        done += n
        if progress_callback:
            progress_callback(done, total)


def _gpu_search_loop(seed_start, seed_count, min_eyes,
                       callback, stop_event, progress_callback,
                       num_workers=1, mc_version=28, twelve_mode=False,
                       use_crunch_prefilter=False, crunch_threshold=0.035,
                       min_max_y=None):
    """
    GPU search pipeline (LAYOUT-BASED, Gaider10 architecture):
      1. CPU multi-thread: pour chaque structure_seed, simule la stronghold
         pour calculer le portal_chunk EXACT → produit des Layouts
      2. GPU: pour chaque Layout, teste les 65536 high16 variations du
         world_seed contre la matrice Gaider à la position EXACTE
      3. CPU multi-thread: validate les candidats avec le pipeline complet

    Bien plus rapide que la version grille 15×15 car on connaît le
    portal_chunk exact (1 test au lieu de 225).

    Note : seed_start/seed_count sont interprétés comme structure_seed_start
    et structure_seed_count puisque l'algo travaille naturellement sur les
    48 bits bas. On scanne donc seed_count*65536 world_seeds au total.
    """
    import gaider_cuda
    import gaider_layouts

    if not gaider_cuda.is_available():
        raise RuntimeError("CUDA not available")

    # ── MC 1.13-1.17 : kernel LCG full_scan (per-seed : position + grille 15x15
    #    sur GPU). Pas besoin des layouts (pipeline xoroshiro). Scan contigu des
    #    seeds ; validation CPU finale = exactitude garantie.
    if mc_version < 22 and not (gaider_layouts.has_pre118() and gaider_cuda.has_lcg()):
        # Pas de layouts pre118 OU pas de kernel LCG -> fallback full_scan (grille
        # 15x15). Plus lent mais marche des que le kernel LCG est compile.
        if not gaider_cuda.has_lcg():
            raise RuntimeError("GPU LCG kernel (1.13-1.17) absent -> fallback CPU")
        _MAXC = 1_000_000
        _BATCH16 = 1 << 22
        _total = seed_count if seed_count > 0 else (1 << 62)
        _done = 0
        while _done < _total:
            if stop_event is not None and stop_event.is_set():
                break
            _nb = min(_BATCH16, _total - _done)
            _ss = seed_start + _done
            if twelve_mode:
                _t, _cseeds = gaider_cuda.full_scan_12eye(
                    _ss, _nb, mc_version, max_results=_MAXC)
            else:
                _t, _cpairs = gaider_cuda.full_scan_min_eyes(
                    _ss, _nb, min_eyes, mc_version, max_results=_MAXC)
                _cseeds = [s for s, _e in _cpairs]
            if _cseeds:
                _validate_candidates_cpu(
                    _cseeds, min_eyes, callback,
                    num_workers=num_workers, mc_version=mc_version,
                    min_max_y=min_max_y, stop_event=stop_event)
            _done += _nb
            if progress_callback:
                progress_callback(_done, seed_count)
        return

    if not gaider_layouts.is_available():
        raise RuntimeError(
            f"gaider_layouts unavailable: {gaider_layouts.get_load_error()}")

    # Pipeline GPU layout-based (architecture Gaider10) :
    # - On itère sur les structure_seeds (les 48 bits bas du world_seed).
    # - Pour CHAQUE structure_seed, le GPU teste les 65536 valeurs de high16
    #   (les 16 bits hauts) — donc 65536 world_seeds testés par struct_seed.
    # - `seed_start` est interprété comme "structure_seed_start" (les 48
    #   bits bas) ; le high16 est balayé en intégralité pour chaque
    #   struct_seed. Conséquence : les seeds résultantes ne sont pas
    #   monotones — pour une même struct_seed on peut trouver une seed
    #   high16=0 (positive) puis une seed high16=0xFFFF (très négative).
    #   C'est l'algorithme Gaider10 et c'est par design.
    seed_start_u64 = seed_start & ((1 << 64) - 1)
    struct_seed_start = seed_start_u64 & ((1 << 48) - 1)

    # seed_count est le nombre de world_seeds à scanner. Avec 65536 high16
    # par struct_seed, on couvre seed_count // 65536 struct_seeds.
    # Si seed_count == 0 (= illimité), on tourne jusqu'au stop_event.
    if seed_count <= 0:
        n_struct_seeds = (1 << 48)   # plage entière des struct_seeds (48 bits)
    else:
        n_struct_seeds = max(1, seed_count // 65536)

    # Tailles : 16k struct seeds → ~11k layouts → ~1 milliard world_seeds
    # par batch (avec ~0.7 layout par struct seed).
    # NOTE: on garde des batches assez petits pour que progress_callback soit
    # appelé ~1x/s (au lieu d'1x tous les ~4 milliards de seeds), ce qui donne
    # un SPS "live" au lieu de 0 sps figé pendant tout le batch. La saturation
    # GPU n'en souffre pas : chaque struct seed lance déjà 65536 threads high16,
    # donc 16384 struct = ~1 milliard de threads par grille (largement de quoi
    # saturer n'importe quel GPU NVIDIA).
    BATCH_STRUCT = 16384           # struct seeds par batch
    MAX_CAND = 1_000_000

    # ── Crunchiness pre-filter (optional, mushroom islands) ───────────────
    # When use_crunch_prefilter=True, we use gaider_cuda.crunchiness_filter()
    # to scan the raw world-seed space BEFORE the Gaider layout pipeline.
    # This gives a list of candidate world-seeds that pass the yo-score filter
    # (ported from COMMISSION's KernelFilterSeeds).  We then validate those
    # seeds normally via CPU cubiomes.
    #
    # NOTE: This replaces the full _gpu_search_loop pipeline for the crunch
    # path — the layout-based Gaider scan is NOT run in this branch because
    # the crunch filter targets mushroom islands (biome search), not stronghold
    # positions.  The two filters are independent.
    #
    # TODO (future work): chain crunch → Gaider for seeds that need BOTH a
    # mushroom island AND a specific stronghold configuration.
    if use_crunch_prefilter:
        try:
            _run_crunch_search(
                seed_start, seed_count,
                callback, stop_event, progress_callback,
                crunch_threshold=crunch_threshold,
                min_eyes=min_eyes, mc_version=mc_version,
            )
        except Exception as _ce:
            import traceback
            traceback.print_exc()
            print(f"[Crunchiness pre-filter failed: {_ce}] — skipping")
        return

    done = 0
    while done < n_struct_seeds:
        if stop_event is not None and stop_event.is_set():
            break

        n = min(BATCH_STRUCT, n_struct_seeds - done)
        ss_start = (struct_seed_start + done) & ((1 << 48) - 1)

        # Step 1 : CPU layouts (multi-threaded)
        layouts_buf, n_layouts = gaider_layouts.generate_layouts_parallel(
            ss_start, n, num_workers=num_workers, pre118=_use_old_filler(mc_version))

        if n_layouts == 0:
            done += n
            if progress_callback:
                progress_callback(min(done * 65536, seed_count) if seed_count > 0 else done * 65536, seed_count)
            continue

        # Step 2 : GPU scan
        if twelve_mode:
            total, cand_seeds = gaider_cuda.layout_scan_12eye(
                layouts_buf, n_layouts, mc_version=mc_version, max_results=MAX_CAND)
            cand_pairs = [(s, 12) for s in cand_seeds]
        else:
            total, cand_pairs = gaider_cuda.layout_scan_min_eyes(
                layouts_buf, n_layouts, min_eyes, mc_version=mc_version, max_results=MAX_CAND)

        # Step 3 : CPU validation
        if cand_pairs:
            cand_seeds = [s for s, _ in cand_pairs]
            _validate_candidates_cpu(
                cand_seeds, min_eyes, callback,
                num_workers=num_workers, mc_version=mc_version,
                min_max_y=min_max_y, stop_event=stop_event)

        done += n
        if progress_callback:
            # Report progress in units of world_seeds (66536 per struct seed)
            progress_callback(min(done * 65536, seed_count) if seed_count > 0 else done * 65536, seed_count)


def _validate_candidates_cpu(candidate_seeds, min_eyes, callback,
                              num_workers=1, mc_version=28, min_max_y=None,
                              stop_event=None):
    """Valide une liste de seeds candidates via le pipeline complet
    (cubiomes biome filter + count_stronghold_eyes + double-check Gaider).
    Utilise un ProcessPoolExecutor pour répartir sur plusieurs cores."""
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from search_worker import search_chunk

    # On crée un job par groupe de ~50 candidats (validation prend ~15 ms
    # par candidat avec biome filter, donc 50 candidats = 750 ms).
    GROUP = 50
    args_list = []
    for i in range(0, len(candidate_seeds), GROUP):
        group = candidate_seeds[i:i+GROUP]
        # On trick search_chunk en passant des seeds discontinus via une
        # liste explicite. Pour ça on appelle directement la fonction de
        # validation.
        args_list.append(group)

    def _stopped():
        return stop_event is not None and stop_event.is_set()

    if num_workers <= 1:
        # Mono-thread
        for group in args_list:
            if _stopped():
                return
            for seed in group:
                if _stopped():
                    return
                _validate_one(seed, min_eyes, callback, mc_version, min_max_y)
    else:
        # Multi-process : chaque worker valide un groupe. IMPORTANT : on n'utilise
        # PAS `with ... as pool` car sa sortie fait shutdown(wait=True) → bloque
        # jusqu'a la fin de TOUS les candidats meme apres Stop. On gere le
        # shutdown a la main (non bloquant a l'arret).
        pool = ProcessPoolExecutor(max_workers=min(num_workers, 61))   # Windows limit: 61
        try:
            futures = [pool.submit(_validate_group, group, min_eyes, mc_version, min_max_y)
                       for group in args_list]
            for fut in as_completed(futures):
                if _stopped():
                    for f in futures:
                        f.cancel()
                    break
                try:
                    matches = fut.result()
                    for item in matches:
                        callback(*item)
                except Exception:
                    pass
        finally:
            try:
                pool.shutdown(wait=not _stopped(), cancel_futures=True)
            except TypeError:
                pool.shutdown(wait=not _stopped())


def _validate_one(seed, min_eyes, callback, mc_version, min_max_y=None):
    """Valide une seed avec le pipeline complet et appelle callback si OK.
    Utilisé en mode mono-thread."""
    from search_worker import _get_cubiomes
    from stronghold_eyes import count_stronghold_eyes
    try:
        lib = _get_cubiomes()
        lib.setup_and_seed(mc=mc_version, seed=seed)
        sh_list = lib.find_strongholds(max_count=1, radius_blocks=None)
        if not sh_list:
            return
        bx, bz = sh_list[0]
        eyes = count_stronghold_eyes(seed, bx, bz, mc_version)
        if eyes < min_eyes:
            return
        # (26/09) no "Gaider double-check": the gaider-only count ignores the
        # pieces before the portal room and the 12-eye any-chunk rule, so it
        # dropped real hits (e.g. 12/12 portals across two chunks)
        _maxy = None
        if min_max_y is not None:
            _info = get_stronghold_portal_full(seed, bx, bz, mc_version)
            _maxy = _info["highest_y"] if _info is not None else None
            if _maxy is None or _maxy < min_max_y:
                return
        callback(seed, eyes, bx, bz, _maxy)
    except Exception:
        pass


def _validate_group(seeds, min_eyes, mc_version, min_max_y=None):
    """Worker process: valide un groupe de seeds. Retourne les matches."""
    from search_worker import _get_cubiomes
    from stronghold_eyes import count_stronghold_eyes
    matches = []
    lib = _get_cubiomes()
    for seed in seeds:
        try:
            lib.setup_and_seed(mc=mc_version, seed=seed)
            sh_list = lib.find_strongholds(max_count=1, radius_blocks=None)
            if not sh_list:
                continue
            bx, bz = sh_list[0]
            eyes = count_stronghold_eyes(seed, bx, bz, mc_version)
            if eyes < min_eyes:
                continue
            # (26/09) no "Gaider double-check" — see _validate_one
            _maxy = None
            if min_max_y is not None:
                _info = get_stronghold_portal_full(seed, bx, bz, mc_version)
                _maxy = _info["highest_y"] if _info is not None else None
                if _maxy is None or _maxy < min_max_y:
                    continue
            matches.append((seed, eyes, bx, bz, _maxy))
        except Exception:
            pass
    return matches


_SL_WORKER_LIB = None   # per-process cubiomes lib, created once and reused


def _seed_list_eye_worker(args):
    """Worker for SEED-LIST mode (eye / Max Y): test exactly the given full seeds
    at their REAL (biome-accurate) 1st stronghold — the SAME position cubiomes /
    the map / the 'All strongholds' dialog show, so the eyes & Max Y match what
    SSA displays. (get_first_stronghold_pos is a biome-FREE estimate that lands on
    the wrong chunk for many seeds → phantom layout → wrong counts.) Returns the
    matching (seed, eyes, bx, bz, maxy)."""
    global _SL_WORKER_LIB
    sublist, min_eyes, min_max_y, mc = args
    out = []
    # Build the cubiomes lib ONCE per worker process (not once per chunk) so
    # small chunks stay cheap.
    if _SL_WORKER_LIB is None:
        try:
            import cubiomes_wrap as _cw
            _SL_WORKER_LIB = _cw.make_worker_lib()
        except Exception:
            _SL_WORKER_LIB = False
    clib = _SL_WORKER_LIB or None
    for seed in sublist:
        try:
            if clib is not None:
                clib.setup_and_seed(mc, seed)
                shs = clib.find_strongholds(max_count=1)
                if not shs:
                    continue
                bx, bz = shs[0]
            else:
                bx, bz = get_first_stronghold_pos(seed)
            eyes, maxy = get_stronghold_eyes_and_maxy(seed, bx, bz, mc)
        except Exception:
            continue
        if eyes < min_eyes:
            continue
        if min_max_y is not None and (maxy is None or maxy < min_max_y):
            continue
        out.append((seed, eyes, bx, bz, maxy))
    return out


_SL_NATIVE = None   # per-process: (cdll, ctx, glib) or False if natives missing


def _sl_native_init():
    """Bind the native seedlist primitives once per worker process. Returns
    (cdll, ctx, glib) or False if either native is unavailable (old DLL)."""
    global _SL_NATIVE
    if _SL_NATIVE is not None:
        return _SL_NATIVE
    try:
        import ctypes as C
        import cubiomes_wrap as _cw
        import gaider_layouts as _gl
        clib = _cw.make_worker_lib()
        cdll = clib._lib
        _gl._load()
        glib = _gl._LIB
        if (not hasattr(cdll, "seedlist_first_stronghold")
                or glib is None or not hasattr(glib, "max_y_batch_arr")):
            _SL_NATIVE = False
            return False
        cdll.cb_create.restype = C.c_void_p
        cdll.seedlist_first_stronghold.argtypes = [
            C.c_void_p, C.c_int, C.POINTER(C.c_int64), C.c_int,
            C.POINTER(C.c_int32), C.POINTER(C.c_int32)]
        cdll.seedlist_first_stronghold.restype = C.c_int
        glib.max_y_batch_arr.argtypes = [
            C.POINTER(C.c_int64), C.c_int32, C.POINTER(C.c_int32),
            C.POINTER(C.c_int32), C.c_int, C.c_int, C.c_int, C.c_int,
            C.c_int, C.c_int, C.POINTER(C.c_int64), C.POINTER(C.c_int32)]
        glib.max_y_batch_arr.restype = C.c_uint32
        # Eye prefilter (seedlist eye finder) — optional (newer DLL only).
        if hasattr(cdll, "seedlist_eyes_prefilter_arr"):
            cdll.seedlist_eyes_prefilter_arr.argtypes = [
                C.POINTER(C.c_int64), C.POINTER(C.c_int32), C.POINTER(C.c_int32),
                C.c_int32, C.c_int, C.c_int, C.c_int,
                C.POINTER(C.c_int64), C.POINTER(C.c_int32)]
            cdll.seedlist_eyes_prefilter_arr.restype = C.c_int
        # All-128 strongholds (flattened) — optional (newer DLL only).
        if hasattr(cdll, "cb_strongholds128_arr"):
            cdll.cb_strongholds128_arr.argtypes = [
                C.c_void_p, C.c_int, C.POINTER(C.c_int64), C.c_int,
                C.POINTER(C.c_uint64), C.POINTER(C.c_int32), C.POINTER(C.c_int32),
                C.c_int]
            cdll.cb_strongholds128_arr.restype = C.c_int
        ctx = cdll.cb_create()
        _SL_NATIVE = (cdll, ctx, glib)
    except Exception:
        _SL_NATIVE = False
    return _SL_NATIVE


def _seed_list_maxy_native_worker(args):
    """Native SEED-LIST worker for Max Y filtering. The whole chunk runs in C:
    snapped first-stronghold positions (cubiomes) + topY (gaider) over the array,
    then Python validates ONLY the survivors (topY >= min_max_y) for exact eyes.
    ~15x the pure-Python per-seed path. Falls back to _seed_list_eye_worker if the
    natives aren't compiled in. Returns matching (seed, eyes, bx, bz, maxy)."""
    nat = _sl_native_init()
    if not nat:
        return _seed_list_eye_worker(args)
    import ctypes as C
    import numpy as np
    cdll, ctx, glib = nat
    import cubiomes_wrap as _cw_l; _cw_l.apply_large_ctx(cdll, ctx)   # large biomes world
    sublist, min_eyes, min_max_y, mc = args
    seeds = np.asarray(sublist, dtype=np.int64)
    n = int(len(seeds))
    if n == 0:
        return []
    bx = np.zeros(n, dtype=np.int32)
    bz = np.zeros(n, dtype=np.int32)
    cdll.seedlist_first_stronghold(
        ctx, int(mc), seeds.ctypes.data_as(C.POINTER(C.c_int64)), n,
        bx.ctypes.data_as(C.POINTER(C.c_int32)),
        bz.ctypes.data_as(C.POINTER(C.c_int32)))
    cxs = (bx >> 4).astype(np.int32)
    czs = (bz >> 4).astype(np.int32)
    sea, miny = _sea_min_for_mc(mc)
    thr = int(min_max_y) if min_max_y is not None else -1_000_000
    out_s = np.zeros(n, dtype=np.int64)
    out_y = np.zeros(n, dtype=np.int32)
    cnt = int(glib.max_y_batch_arr(
        seeds.ctypes.data_as(C.POINTER(C.c_int64)), n,
        cxs.ctypes.data_as(C.POINTER(C.c_int32)),
        czs.ctypes.data_as(C.POINTER(C.c_int32)),
        1 if _use_old_filler(mc) else 0, sea, 10, miny,
        _inflate_for_mc(mc), thr,
        out_s.ctypes.data_as(C.POINTER(C.c_int64)),
        out_y.ctypes.data_as(C.POINTER(C.c_int32))))
    pos = {int(seeds[i]): (int(bx[i]), int(bz[i])) for i in range(n)}
    out = []
    for k in range(cnt):
        s = int(out_s[k])
        sbx, sbz = pos[s]
        # exact eyes on the (few) survivors only
        eyes, _maxy = get_stronghold_eyes_and_maxy(s, sbx, sbz, mc)
        if eyes < min_eyes:
            continue
        out.append((s, eyes, sbx, sbz, int(out_y[k])))
    return out


def _seed_list_eye_native_worker(args):
    """Native SEED-LIST worker for the EYE finder (no Max Y filter). Native snapped
    positions + native single-chunk eye prefilter (max over a grid) over the whole
    chunk; then Python validates survivors exactly (count via _eyes_per_frame, which
    applies the verified 12-eye any-chunk rule). Exact for the 12-eye case; for the
    rarer < 12 multi-chunk case the grid is a prefilter (same as the range path).
    Falls back to the pure-Python worker if the native isn't available."""
    nat = _sl_native_init()
    if not nat:
        return _seed_list_eye_worker(args)
    import ctypes as C
    import numpy as np
    cdll, ctx, glib = nat
    import cubiomes_wrap as _cw_l; _cw_l.apply_large_ctx(cdll, ctx)   # large biomes world
    if not hasattr(cdll, "seedlist_eyes_prefilter_arr"):
        return _seed_list_eye_worker(args)
    sublist, min_eyes, min_max_y, mc = args
    seeds = np.asarray(sublist, dtype=np.int64)
    n = int(len(seeds))
    if n == 0:
        return []
    bx = np.zeros(n, dtype=np.int32)
    bz = np.zeros(n, dtype=np.int32)
    cdll.seedlist_first_stronghold(
        ctx, int(mc), seeds.ctypes.data_as(C.POINTER(C.c_int64)), n,
        bx.ctypes.data_as(C.POINTER(C.c_int32)),
        bz.ctypes.data_as(C.POINTER(C.c_int32)))
    cxs = (bx >> 4).astype(np.int32)
    czs = (bz >> 4).astype(np.int32)
    out_s = np.zeros(n, dtype=np.int64)
    out_e = np.zeros(n, dtype=np.int32)
    # ±15 chunks: the start snap (≤7) + the portal-room offset (≤12 seen) — the
    # range path's _XRSR_GRID. ±7 lost real results here (audit 28/09: ≥5 eyes,
    # 4 of 23 seeds; "Test seed" said "no match" for real 5-eye seeds).
    GRID = 15
    cnt = int(cdll.seedlist_eyes_prefilter_arr(
        seeds.ctypes.data_as(C.POINTER(C.c_int64)),
        cxs.ctypes.data_as(C.POINTER(C.c_int32)),
        czs.ctypes.data_as(C.POINTER(C.c_int32)),
        n, int(mc), int(min_eyes), GRID,
        out_s.ctypes.data_as(C.POINTER(C.c_int64)),
        out_e.ctypes.data_as(C.POINTER(C.c_int32))))
    pos = {int(seeds[i]): (int(bx[i]), int(bz[i])) for i in range(n)}
    out = []
    for k in range(cnt):
        s = int(out_s[k])
        sbx, sbz = pos[s]
        # exact eyes (+ maxy) on the candidates only
        eyes, maxy = get_stronghold_eyes_and_maxy(s, sbx, sbz, mc)
        if eyes < min_eyes:
            continue
        out.append((s, eyes, sbx, sbz, maxy))
    return out


def _seed_list_128_python(args):
    """Pure-Python fallback for the all-128 seedlist scan (correct, slow)."""
    sublist, min_eyes, min_max_y, mc = args
    try:
        import cubiomes_wrap as _cw
        clib = _cw.make_worker_lib()
    except Exception:
        return []
    out = []
    for seed in sublist:
        seed = int(seed)
        try:
            clib.setup_and_seed(mc, seed)
            shs = clib.find_strongholds(max_count=128)
        except Exception:
            continue
        for (bx, bz) in shs:
            try:
                eyes, maxy = get_stronghold_eyes_and_maxy(seed, bx, bz, mc)
            except Exception:
                continue
            if eyes < min_eyes:
                continue
            if min_max_y is not None and (maxy is None or maxy < min_max_y):
                continue
            out.append((seed, eyes, bx, bz, maxy))
    return out


def _seed_list_128_worker(args):
    """Scan ALL strongholds (up to 128) of each seed — native, 100% accurate.
    Native flattened positions (cb_strongholds128_arr) + native metric over every
    stronghold (topY if a Max Y filter, else the grid eye count), then exact Python
    validation of the survivors. Emits one hit per stronghold satisfying the filter
    (a seed can appear several times). Falls back to pure Python if no native DLL."""
    nat = _sl_native_init()
    if not nat:
        return _seed_list_128_python(args)
    import ctypes as C
    import numpy as np
    cdll, ctx, glib = nat
    import cubiomes_wrap as _cw_l; _cw_l.apply_large_ctx(cdll, ctx)   # large biomes world
    if not hasattr(cdll, "cb_strongholds128_arr"):
        return _seed_list_128_python(args)
    sublist, min_eyes, min_max_y, mc = args
    seeds = np.asarray(sublist, dtype=np.int64)
    n = int(len(seeds))
    if n == 0:
        return []
    CAP = n * 128
    fseed = np.zeros(CAP, dtype=np.uint64)
    fx = np.zeros(CAP, dtype=np.int32)
    fz = np.zeros(CAP, dtype=np.int32)
    k = int(cdll.cb_strongholds128_arr(
        ctx, int(mc), seeds.ctypes.data_as(C.POINTER(C.c_int64)), n,
        fseed.ctypes.data_as(C.POINTER(C.c_uint64)),
        fx.ctypes.data_as(C.POINTER(C.c_int32)),
        fz.ctypes.data_as(C.POINTER(C.c_int32)), CAP))
    k = min(k, CAP)
    if k == 0:
        return []
    fseed = fseed[:k]
    fx = fx[:k]
    fz = fz[:k]
    fcx = (fx >> 4).astype(np.int32)
    fcz = (fz >> 4).astype(np.int32)
    fss = fseed.astype(np.int64)            # uint64 -> signed int64 (reinterpret)
    out_s = np.zeros(k, dtype=np.int64)
    if min_max_y is not None:
        sea, miny = _sea_min_for_mc(mc)
        out_y = np.zeros(k, dtype=np.int32)
        glib.max_y_batch_arr(                # threshold -inf -> out_y[i] aligned
            fss.ctypes.data_as(C.POINTER(C.c_int64)), k,
            fcx.ctypes.data_as(C.POINTER(C.c_int32)),
            fcz.ctypes.data_as(C.POINTER(C.c_int32)),
            1 if _use_old_filler(mc) else 0, sea, 10, miny,
            _inflate_for_mc(mc), -1_000_000,
            out_s.ctypes.data_as(C.POINTER(C.c_int64)),
            out_y.ctypes.data_as(C.POINTER(C.c_int32)))
        passing = np.nonzero(out_y >= int(min_max_y))[0]
    else:
        if not hasattr(cdll, "seedlist_eyes_prefilter_arr"):
            return _seed_list_128_python(args)
        out_e = np.zeros(k, dtype=np.int32)
        cdll.seedlist_eyes_prefilter_arr(    # min_eyes=0 -> out_e[i] = grid eye count
            fss.ctypes.data_as(C.POINTER(C.c_int64)),
            fcx.ctypes.data_as(C.POINTER(C.c_int32)),
            fcz.ctypes.data_as(C.POINTER(C.c_int32)),
            k, int(mc), 0, 15,   # ±15 chunks (±7 missed portals: audit 28/09)
            out_s.ctypes.data_as(C.POINTER(C.c_int64)),
            out_e.ctypes.data_as(C.POINTER(C.c_int32)))
        passing = np.nonzero(out_e >= int(min_eyes))[0]
    out = []
    for i in passing.tolist():
        s = int(fss[i]); sbx = int(fx[i]); sbz = int(fz[i])
        eyes, maxy = get_stronghold_eyes_and_maxy(s, sbx, sbz, mc)
        if eyes < min_eyes:
            continue
        if min_max_y is not None and (maxy is None or maxy < min_max_y):
            continue
        out.append((s, eyes, sbx, sbz, maxy))
    return out


def _eyes_128_from_positions_worker(args):
    """Eye-count (and optional Max Y) at GPU-computed 128-stronghold positions.
    Same exact logic as _seed_list_128_worker, but the positions are supplied
    (computed on GPU by biome_gpu) instead of via cb_strongholds128_arr.
    args = (fss, fx, fz, min_eyes, min_max_y, mc); arrays are flat (one entry per
    stronghold). Returns list of (seed, eyes, bx, bz, maxy)."""
    import ctypes as C
    import numpy as np
    fss, fx, fz, min_eyes, min_max_y, mc = args
    fss = np.ascontiguousarray(fss, dtype=np.int64)
    fx  = np.ascontiguousarray(fx,  dtype=np.int32)
    fz  = np.ascontiguousarray(fz,  dtype=np.int32)
    k = int(len(fss))
    if k == 0:
        return []

    def _py_exact():
        o = []
        for i in range(k):
            s = int(fss[i]); bx = int(fx[i]); bz = int(fz[i])
            try:
                eyes, maxy = get_stronghold_eyes_and_maxy(s, bx, bz, mc)
            except Exception:
                continue
            if eyes < min_eyes:
                continue
            if min_max_y is not None and (maxy is None or maxy < min_max_y):
                continue
            o.append((s, eyes, bx, bz, maxy))
        return o

    nat = _sl_native_init()
    if not nat:
        return _py_exact()
    cdll, ctx, glib = nat
    import cubiomes_wrap as _cw_l; _cw_l.apply_large_ctx(cdll, ctx)   # large biomes world
    fcx = (fx >> 4).astype(np.int32)
    fcz = (fz >> 4).astype(np.int32)
    out_s = np.zeros(k, dtype=np.int64)
    if min_max_y is not None:
        sea, miny = _sea_min_for_mc(mc)
        out_y = np.zeros(k, dtype=np.int32)
        glib.max_y_batch_arr(
            fss.ctypes.data_as(C.POINTER(C.c_int64)), k,
            fcx.ctypes.data_as(C.POINTER(C.c_int32)),
            fcz.ctypes.data_as(C.POINTER(C.c_int32)),
            1 if _use_old_filler(mc) else 0, sea, 10, miny,
            _inflate_for_mc(mc), -1_000_000,
            out_s.ctypes.data_as(C.POINTER(C.c_int64)),
            out_y.ctypes.data_as(C.POINTER(C.c_int32)))
        passing = np.nonzero(out_y >= int(min_max_y))[0]
    else:
        if not hasattr(cdll, "seedlist_eyes_prefilter_arr"):
            return _py_exact()
        out_e = np.zeros(k, dtype=np.int32)
        cdll.seedlist_eyes_prefilter_arr(    # min_eyes=0 -> out_e[i] = grid eye count
            fss.ctypes.data_as(C.POINTER(C.c_int64)),
            fcx.ctypes.data_as(C.POINTER(C.c_int32)),
            fcz.ctypes.data_as(C.POINTER(C.c_int32)),
            k, int(mc), 0, 15,   # ±15 chunks (±7 missed portals: audit 28/09)
            out_s.ctypes.data_as(C.POINTER(C.c_int64)),
            out_e.ctypes.data_as(C.POINTER(C.c_int32)))
        passing = np.nonzero(out_e >= int(min_eyes))[0]
    out = []
    for i in passing.tolist():
        s = int(fss[i]); bx = int(fx[i]); bz = int(fz[i])
        eyes, maxy = get_stronghold_eyes_and_maxy(s, bx, bz, mc)
        if eyes < min_eyes:
            continue
        if min_max_y is not None and (maxy is None or maxy < min_max_y):
            continue
        out.append((s, eyes, bx, bz, maxy))
    return out


def _exact_eyes_worker(args):
    """Exact eye (and optional Max Y) validation at the GPU-survivor positions only.
    The GPU already eye-prefiltered every stronghold, so callers pass ONLY the few
    survivors (prefilter >= min_eyes) -> the CPU barely works. Pure Python (no native
    dependency); returns (seed, eyes, bx, bz, maxy) hits."""
    fss, fx, fz, min_eyes, min_max_y, mc = args
    out = []
    for i in range(len(fss)):
        s = int(fss[i]); bx = int(fx[i]); bz = int(fz[i])
        try:
            eyes, maxy = get_stronghold_eyes_and_maxy(s, bx, bz, mc)
        except Exception:
            continue
        if eyes < min_eyes:
            continue
        if min_max_y is not None and (maxy is None or maxy < min_max_y):
            continue
        out.append((s, eyes, bx, bz, maxy))
    return out


def _biome_gpu_128_pipeline(src, total, min_eyes, min_max_y, mc, nw,
                            callback, stop_event, progress_callback):
    """GPU biome-snap pipeline for the ALL-128 seedlist scan (1.13-1.17).

    The GPU computes, per seed, the 128 EXACT stronghold positions (biome snap, the
    bottleneck) AND a fused eye prefilter (grid-max, 0..12) — both on the card. The
    CPU then only EXACT-validates the rare survivors (prefilter >= min_eyes), so it
    stays nearly idle (no 12-thread overnight burn). 100% accurate. Falls back to the
    CPU 128 worker if the GPU is unavailable or a Max Y filter is set."""
    import biome_gpu
    import numpy as np
    from itertools import islice
    from collections import deque
    from concurrent.futures import ProcessPoolExecutor

    GPU_BATCH = 20000     # seeds per GPU call (the host sub-batches by VRAM internally)
    VAL_CHUNK = 2000      # survivor strongholds per CPU validation job
    done = 0

    def _report():
        if progress_callback:
            progress_callback(done, total if total is not None else done)

    pool = ProcessPoolExecutor(max_workers=min(nw, 61))   # Windows limit: 61
    try:
        in_flight = deque()

        def _drain_one():
            fut = in_flight.popleft()
            try:
                for h in fut.result():
                    callback(*h)
            except Exception:
                pass

        while True:
            if stop_event is not None and stop_event.is_set():
                break
            batch = list(islice(src, GPU_BATCH))
            if not batch:
                break
            # GPU only helps the eye path; Max Y (or GPU failure) -> CPU 128 worker.
            res = None if min_max_y is not None else biome_gpu.strongholds128(mc, batch)
            if res is None:
                for i in range(0, len(batch), 256):
                    sub = batch[i:i + 256]
                    in_flight.append(pool.submit(
                        _seed_list_128_worker, (sub, min_eyes, min_max_y, mc)))
            else:
                xs, zs, es = res
                seeds_arr = np.asarray(batch, dtype=np.int64)
                si, sj = np.nonzero(es >= int(min_eyes))   # survivors: prefilter >= min_eyes
                if len(si):
                    fss = np.ascontiguousarray(seeds_arr[si], dtype=np.int64)
                    fx = np.ascontiguousarray(xs[si, sj], dtype=np.int32)
                    fz = np.ascontiguousarray(zs[si, sj], dtype=np.int32)
                    for k in range(0, len(fss), VAL_CHUNK):
                        sl = slice(k, k + VAL_CHUNK)
                        in_flight.append(pool.submit(
                            _exact_eyes_worker,
                            (fss[sl], fx[sl], fz[sl], min_eyes, min_max_y, mc)))
            done += len(batch)
            _report()
            while len(in_flight) >= nw * 4:
                _drain_one()
        while in_flight:
            if stop_event is not None and stop_event.is_set():
                for f in in_flight:
                    f.cancel()
                break
            _drain_one()
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    _report()


def _seed_list_positions_worker(args):
    """GPU mode: native snapped stronghold WORLD positions for a chunk (runs in a
    worker process -> parallel). Returns (n_seeds, seed_flat, x_flat, z_flat):
    1 entry/seed, or up to 128/seed if scan_128. The MAIN process then runs the
    GPU eye-count on these arrays."""
    import numpy as np
    sublist, mc, scan_128 = args
    seeds = np.asarray(sublist, dtype=np.int64)
    n = int(len(seeds))
    empty = (n, np.zeros(0, np.int64), np.zeros(0, np.int32), np.zeros(0, np.int32))
    nat = _sl_native_init()
    if n == 0 or not nat:
        return empty
    import ctypes as C
    cdll, ctx, glib = nat
    import cubiomes_wrap as _cw_l; _cw_l.apply_large_ctx(cdll, ctx)   # large biomes world
    if scan_128 and hasattr(cdll, "cb_strongholds128_arr"):
        CAP = n * 128
        fs = np.zeros(CAP, np.uint64)
        fx = np.zeros(CAP, np.int32)
        fz = np.zeros(CAP, np.int32)
        k = int(cdll.cb_strongholds128_arr(
            ctx, int(mc), seeds.ctypes.data_as(C.POINTER(C.c_int64)), n,
            fs.ctypes.data_as(C.POINTER(C.c_uint64)),
            fx.ctypes.data_as(C.POINTER(C.c_int32)),
            fz.ctypes.data_as(C.POINTER(C.c_int32)), CAP))
        k = min(k, CAP)
        return (n, fs[:k].astype(np.int64), fx[:k].copy(), fz[:k].copy())
    if not hasattr(cdll, "seedlist_first_stronghold"):
        return empty
    fx = np.zeros(n, np.int32)
    fz = np.zeros(n, np.int32)
    cdll.seedlist_first_stronghold(
        ctx, int(mc), seeds.ctypes.data_as(C.POINTER(C.c_int64)), n,
        fx.ctypes.data_as(C.POINTER(C.c_int32)),
        fz.ctypes.data_as(C.POINTER(C.c_int32)))
    return (n, seeds.copy(), fx, fz)


def _gpu_eye_pipeline(src, total, min_eyes, mc, scan_128, nw,
                      callback, stop_event, progress_callback):
    """GPU eye finder for seed lists / all-128. Worker processes compute the real
    (biome-snapped) positions in parallel; the MAIN process runs the GPU eye-count
    (grid-max single-chunk = exact for the 12-eye any-chunk rule) and emits hits.
    Max Y is NOT handled here (it stays CPU-native)."""
    import numpy as np
    import gaider_cuda as _gc
    from itertools import islice
    from collections import deque
    from concurrent.futures import ProcessPoolExecutor
    CS = 2000 if scan_128 else 20000     # positions are cheap -> big chunks

    def _next():
        buf = list(islice(src, CS))
        if not buf:
            raise StopIteration
        return (buf, mc, scan_128)

    done = 0

    def _report():
        if progress_callback:
            progress_callback(done, total if total is not None else done)

    def _emit_positions(res):
        nseeds, fseed, fx, fz = res
        if len(fseed):
            fcx = (fx >> 4).astype(np.int32)
            fcz = (fz >> 4).astype(np.int32)
            out_e = _gc.eyes_arr(fseed, fcx, fcz, mc, 7)
            if out_e is not None:
                for i in np.nonzero(out_e >= min_eyes)[0].tolist():
                    callback(int(fseed[i]), int(out_e[i]),
                             int(fx[i]), int(fz[i]), None)
        return nseeds

    pool = ProcessPoolExecutor(max_workers=min(nw, 61))   # Windows limit: 61
    try:
        in_flight = deque()
        for _ in range(nw * 3):
            try:
                in_flight.append(pool.submit(_seed_list_positions_worker, _next()))
            except StopIteration:
                break
        while in_flight:
            if stop_event is not None and stop_event.is_set():
                for f in in_flight:
                    f.cancel()
                break
            fut = in_flight.popleft()
            try:
                done += _emit_positions(fut.result())
                _report()
            except Exception:
                pass
            try:
                in_flight.append(pool.submit(_seed_list_positions_worker, _next()))
            except StopIteration:
                pass
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    _report()


def search_end_portal_seeds(seed_start, seed_count, min_eyes,
                             callback, stop_event=None,
                             progress_callback=None, chunk_size=500,
                             num_workers=1, mc_version=28,
                             fast_mode=False, use_gpu=False,
                             use_crunch_prefilter=False,
                             crunch_threshold=0.035, min_max_y=None,
                             seed_list=None, scan_128=False, exact_eyes=False,
                             ignore_eyes=None):
    """
    Itère sur [seed_start, seed_start+seed_count) et appelle
    callback(seed, eyes, bx, bz) pour chaque seed avec >= min_eyes.

    `fast_mode` : pré-filtre Gaider C natif puis validation CPU.
    `use_gpu`   : si True, exécute le pré-filtre Gaider sur GPU CUDA
                  (massive speedup pour 12/12 hunts). La validation
                  finale reste sur CPU pour zéro faux positif.
    `use_crunch_prefilter` : si True ET use_gpu=True, applique le filtre
                  crunchiness (yo-score de COMMISSION) AVANT la recherche
                  principale. Passe ~1/9400 seeds (threshold=0.035).
                  Conçu pour les recherches de mushroom islands.
    `crunch_threshold` : seuil du yo-score (0.035 par défaut).

    Si `min_eyes == 12`, le worker active automatiquement le mode rapide
    Gaider. Précision quasi parfaite pour cette valeur.
    """
    from concurrent.futures import ProcessPoolExecutor, as_completed

    # Worker function importée depuis le module isolé
    from search_worker import search_chunk

    # "only" = keep EXACTLY `min_eyes` (8 only -> drop 9/10/11/12).
    # "ignore N" = drop strongholds with exactly N eyes (e.g. hide the known
    # 12-eye to see the other strongholds). Both wrap the callback once so they
    # apply to every path (range, seed list, all-128).
    if exact_eyes or ignore_eyes is not None:
        _user_cb = callback
        def callback(*a):           # a = (seed, eyes, bx, bz[, maxy])
            if len(a) > 1:
                e = a[1]
                if ignore_eyes is not None and e == ignore_eyes:
                    return
                if exact_eyes and e != min_eyes:
                    return
            _user_cb(*a)

    # "Scan all 128" on a RANGE: only the seed-list path knows how to scan the
    # 128 strongholds (native cb_strongholds128), the range path silently used
    # the first stronghold only. Expose the range as a lazy seed list instead
    # (same approach as the custom sessions). BUG fixed 26/09.
    if scan_128 and seed_list is None:
        from custom_search import _RangeSeedList
        seed_list = _RangeSeedList(seed_start, seed_count, stop_event)

    # ── SEED-LIST mode ────────────────────────────────────────────────────────
    # Test EXACTLY the given full seeds at their 1st stronghold (eyes / Max Y),
    # then stop. Arbitrary seeds => no native range batch / GPU; per-seed CPU,
    # parallelised over <=12 workers. Works for every mc version.
    if seed_list is not None:
        # `seed_list` may be a list OR a lazy iterator/generator (giant lists are
        # streamed from disk, never fully materialised). We chunk it lazily and
        # use a bounded in-flight window so we NEVER submit all jobs at once
        # (submitting 215k tiny jobs upfront was what pegged one core at 100% and
        # showed 0 sps before any result came back).
        from itertools import islice
        from collections import deque

        # Progress total: known for a list/tuple, unknown (None) for a generator.
        try:
            total = len(seed_list)
        except TypeError:
            total = None

        nw = max(1, int(num_workers))
        # Native fast path when a Max Y filter is set: snapped positions + topY
        # run in C over the whole chunk (~15x the pure-Python per-seed path), so
        # bigger chunks are fine. Pure-Python (no Max Y filter) needs small chunks
        # to keep sps/Stop responsive. The in-flight window below caps concurrent
        # jobs, so small chunks no longer flood the pool.
        if scan_128:
            # Scan ALL strongholds (up to 128) of each seed — 100% accurate,
            # native; ~128x the 1-stronghold work (handles both eye and Max Y).
            worker_fn = _seed_list_128_worker
            CS = 40    # petits chunks → 1er résultat + Stop rapides sur une PLAGE
        elif min_max_y is not None:
            worker_fn = _seed_list_maxy_native_worker
            CS = 4000
        else:
            worker_fn = _seed_list_eye_native_worker   # native; falls back if no DLL
            CS = 4000
        src = iter(seed_list)

        # GPU eye finder (seed list / all-128): worker processes snap positions in
        # parallel; the GPU does the eye-count. ONLY for min_eyes >= 12 — the GPU
        # grid count (max single-chunk) is exact only for the 12-eye any-chunk rule;
        # for < 12 it over-counts, so those go to the CPU path (exact validation).
        # (GPU doesn't actually speed this up — the biome snap dominates — but it
        # stays correct for 12.)
        # NEW: GPU biome-snap for the ALL-128 scan (1.13-1.17). The GPU computes the
        # 128 EXACT stronghold positions per seed (the CPU bottleneck = biome snap);
        # eye-count + exact validation run in parallel CPU workers. Replaces the old
        # positions-bound 128-GPU (which gave ~0 speedup vs CPU). 100% accurate.
        if scan_128 and use_gpu:
            try:
                import biome_gpu as _bg
                if _bg.available() and _bg.supported_version(mc_version):
                    _biome_gpu_128_pipeline(src, total, min_eyes, min_max_y,
                                            mc_version, nw, callback,
                                            stop_event, progress_callback)
                    return
            except Exception:
                import traceback
                traceback.print_exc()
                # fall through to the CPU 128 worker below

        if use_gpu and min_max_y is None and min_eyes >= 12 and not scan_128:
            try:
                import gaider_cuda as _gc
                if _gc.has_eyes_arr():
                    _gpu_eye_pipeline(src, total, min_eyes, mc_version, scan_128,
                                      nw, callback, stop_event, progress_callback)
                    return
            except Exception:
                pass   # GPU unavailable -> fall through to the CPU path

        def _next_args():
            buf = list(islice(src, CS))
            if not buf:
                raise StopIteration
            return (buf, min_eyes, min_max_y, mc_version)

        done = 0

        def _report():
            if progress_callback:
                progress_callback(done, total if total is not None else done)

        if nw == 1:
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                try:
                    args = _next_args()
                except StopIteration:
                    break
                for h in worker_fn(args):
                    callback(*h)
                done += len(args[0])
                _report()
            _report()
            return

        import concurrent.futures as _cf
        pool = ProcessPoolExecutor(max_workers=min(nw, 61))   # Windows limit: 61
        try:
            in_flight = deque()
            IN_FLIGHT_TARGET = nw * 4
            for _ in range(IN_FLIGHT_TARGET):
                try:
                    args = _next_args()
                except StopIteration:
                    break
                in_flight.append((len(args[0]),
                                  pool.submit(worker_fn, args)))
            while in_flight:
                if stop_event is not None and stop_event.is_set():
                    for _, f in in_flight:
                        f.cancel()
                    break
                # On ATTEND le 1er job avec un TIMEOUT court : ainsi le Stop est
                # re-vérifié toutes les 0.2 s même si un worker est lent (au lieu
                # de bloquer indéfiniment sur fut.result() → « stop non fonctionnel »).
                nseeds, fut = in_flight[0]
                try:
                    res = fut.result(timeout=0.2)
                except _cf.TimeoutError:
                    continue
                except Exception:
                    res = []
                in_flight.popleft()
                try:
                    for h in res:
                        callback(*h)
                    done += nseeds
                    _report()
                except Exception:
                    pass
                try:
                    args = _next_args()
                    in_flight.append((len(args[0]),
                                      pool.submit(worker_fn, args)))
                except StopIteration:
                    pass
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        _report()
        return

    # 1.13-1.17 (mc<22) : prefiltres natifs CPU = xoroshiro → chemin CPU
    # version-correct. Le GPU 1.16 n'est utilisé QUE si le kernel LCG est présent
    # dans la DLL CUDA (sinon fallback CPU automatique).
    if mc_version < 22 and use_gpu:
        try:
            import gaider_cuda as _gc
            if not (_gc.is_available() and _gc.has_lcg()):
                use_gpu = False
        except Exception:
            use_gpu = False

    _U64  = (1 << 64) - 1
    # seed_count == 0 → mode illimité (jusqu'au Stop). On utilise une borne
    # très grande (2^48 = espace complet des structure seeds, suffisant).
    if seed_count <= 0:
        total = (1 << 48)
    else:
        total = seed_count

    # Fast paths
    twelve_mode = (min_eyes >= 12)
    use_fast_path = twelve_mode or fast_mode

    # ── GPU PATH ──────────────────────────────────────────────────────
    # En mode GPU on utilise TOUJOURS le pipeline GPU layout-based, peu
    # importe min_eyes. Le kernel CUDA contient son propre pré-filtre
    # Gaider exact (pas besoin de fast_mode). Pour min_eyes=12 c'est le
    # kernel `layout_scan_12eye`, pour 6-11 c'est `layout_scan_min_eyes`.
    # La validation CPU finale garantit zéro faux positif dans tous les cas.
    if use_gpu:
        try:
            return _gpu_search_loop(
                seed_start, seed_count, min_eyes,
                callback, stop_event, progress_callback,
                num_workers=num_workers, mc_version=mc_version,
                twelve_mode=twelve_mode,
                use_crunch_prefilter=use_crunch_prefilter,
                crunch_threshold=crunch_threshold,
                min_max_y=min_max_y)
        except Exception as e:
            # Si le GPU plante, fallback vers le mode CPU.
            import traceback
            traceback.print_exc()
            print(f"[GPU search failed: {e}] — falling back to CPU mode")
            # On continue sur le pipeline CPU normal ci-dessous.

    # ── Mono-thread fast path ─────────────────────────────────────────
    if num_workers <= 1:
        done = 0
        for i in range(0, total, chunk_size):
            if stop_event is not None and stop_event.is_set():
                break
            end = min(i + chunk_size, total)
            _, _, results = search_chunk(
                (seed_start, i, end, min_eyes, mc_version, fast_mode, min_max_y))
            for item in results:
                callback(*item)
            done = end
            if progress_callback:
                progress_callback(done, total)
        return

    # ── Multi-process path ────────────────────────────────────────────
    # Tailles de blocs adaptées au mode :
    #   - 12-eye mode : ~1 µs/seed → blocs 200k+ pour amortir IPC
    #   - fast_mode 6-11 : ~1 µs filtrage + ~12 ms validation par candidat
    #     → blocs plus petits (validation peut être lente)
    #   - slow normal : ~12 ms/seed → blocs ~chunk_size
    if mc_version < 22:
        # 1.13-1.17 : pas de prefiltre natif (chemin lent PATH C, meme en 12-eye).
        # Blocs PETITS pour que progression + resultats s'affichent en continu ;
        # sinon des blocs geants ne se terminent jamais -> "0 scanned" alors que
        # le CPU tourne a fond.
        block = max(100, min(total // (num_workers * 16) + 1, chunk_size))
    elif twelve_mode:
        # capped: with no seed limit (total = 2^48) this asked each job for a
        # 64 TiB buffer (MemoryError, swallowed) → 0 progress for ever; also
        # keeps the int32 `n` of gaider_batch_12eye_with_init in range
        block = min(4_000_000, max(200_000, total // (num_workers * 16) + 1))
    elif fast_mode:
        # En fast_mode 6-11, le coût total dépend du nombre de candidats.
        # Bloc de ~50k seeds = ~50ms de scan C + temps de validation.
        # Pour min=6 : ~5% candidats → 2500 validations × 12ms = 30s/bloc.
        # Pour min=8 : ~0.05% → 25 validations × 12ms = 300ms/bloc. OK.
        # On utilise un bloc qui s'adapte au min_eyes : plus permissif
        # = blocs plus petits pour pas freezer.
        # 26/09: the 1.18+ prefilter now scans a ±15-chunk window (~0.3-0.6 ms
        # per seed): 50k blocks took 15-30 s each -> 0 sps at start and
        # progress in waves of workers x block. ~1 s per block instead.
        if min_eyes >= 7:
            block = 3_000
        else:  # min_eyes == 6 (more candidates to validate)
            block = 1_500
    else:
        # exact path, 1.18+: ~20 ms per seed (biome snap) -> ~2 s blocks
        block = total // (num_workers * 16) + 1
        block = max(20, min(block, 100))

    # Génère les args lazily (important pour mode unlimited où total = 2^48
    # → on ne peut pas pré-allouer args_list en mémoire).
    def _args_iter():
        i = 0
        while i < total:
            end = min(i + block, total)
            yield (seed_start, i, end, min_eyes, mc_version, fast_mode, min_max_y)
            i = end

    done = 0
    pool = None
    args_gen = _args_iter()
    try:
        # Création du pool. Sur Windows (spawn), chaque worker importera
        # `search_worker` qui ne dépend que de `stronghold_eyes` → rapide.
        pool = ProcessPoolExecutor(max_workers=min(num_workers, 61))   # Windows limit: 61

        # Streaming submission : on garde au plus `num_workers * 4` jobs
        # en vol pour éviter de submit trop de jobs d'un coup.
        from collections import deque
        in_flight = deque()
        IN_FLIGHT_TARGET = num_workers * 4

        # Pre-fill : soumet la première vague
        for _ in range(IN_FLIGHT_TARGET):
            try:
                args = next(args_gen)
            except StopIteration:
                break
            in_flight.append(pool.submit(search_chunk, args))

        import concurrent.futures as _cf
        in_flight = set(in_flight)
        while in_flight:
            if stop_event is not None and stop_event.is_set():
                # Annule tous les futures restants (en vol et à venir)
                for f in in_flight:
                    f.cancel()
                break
            # 26/09: wait for ANY finished job with a timeout (was: block on
            # the oldest one) -> Stop reacts within 0.2 s, no progress waves.
            finished, _ = _cf.wait(in_flight, timeout=0.2,
                                   return_when=_cf.FIRST_COMPLETED)
            for fut in finished:
                in_flight.discard(fut)
                try:
                    i_start, i_end, results = fut.result()
                    for item in results:
                        callback(*item)
                    done += (i_end - i_start)
                    if progress_callback:
                        progress_callback(done, total)
                except Exception as _e:
                    # a crashed job must STOP the search with an error: before it
                    # was printed and skipped, and the search ended "done" with a
                    # hole in the range (e.g. seeds crossing 2^63)
                    for f in in_flight:
                        f.cancel()
                    raise _WorkerFailed(f"a search job failed: {type(_e).__name__}: {_e}") from _e
                # Soumet le prochain job pour garder le pool plein
                try:
                    args = next(args_gen)
                    in_flight.add(pool.submit(search_chunk, args))
                except StopIteration:
                    pass
    except _WorkerFailed:
        raise
    except Exception:
        # ProcessPool a foiré complètement (pickle, fork, OS error).
        # Fallback sur mono-thread pour ne pas tout casser — only before any
        # job completed (it restarts at the range start)
        if done:
            raise
        if pool is not None:
            try:
                pool.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
            pool = None
        # Mono-thread fallback (avec stop check pour mode unlimited)
        i = 0
        while i < total:
            if stop_event is not None and stop_event.is_set():
                break
            end = min(i + chunk_size, total)
            _, _, results = search_chunk(
                (seed_start, i, end, min_eyes, mc_version, fast_mode, min_max_y))
            for item in results:
                callback(*item)
            if progress_callback:
                progress_callback(end, total)
            i = end
    finally:
        if pool is not None:
            try:
                stopping = stop_event is not None and stop_event.is_set()
                pool.shutdown(wait=not stopping, cancel_futures=True)
            except Exception:
                pass



def eyes_maxy_batch(seed, mc, positions):
    """[(x, z), …] -> {(x, z): (eyes, top_y)}. Entry point for the map's helper
    process (qtui/map_engine.py): the pure-Python count holds the GIL for ~1 s
    per 128 strongholds, which made the UI stutter when run in a thread."""
    out = {}
    for x, z in positions:
        try:
            out[(int(x), int(z))] = get_stronghold_eyes_and_maxy(int(seed), int(x), int(z), int(mc))
        except Exception:
            pass
    return out
