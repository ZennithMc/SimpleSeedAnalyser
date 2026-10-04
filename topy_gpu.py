"""
topy_gpu.py — Top Y of whole 48-bit families on the GPU (03/10, prototype).

The GPU computes the layout of EVERY stronghold a family's snap can reach
(15 × 15 start chunks, one thread each) and returns only the (family, chunk)
whose Top Y reaches the threshold. Everything after that stays on the CPU and
exact: the snap of the 65 536 seeds of the family, and the full re-check of the
seed that lands on the chunk (topy_family.family_hits does both).

The generator (cubiomes_src/topy_gpu_gen.cuh) is a line-by-line port of
StrongholdGenerator::topY, checked against it (tools/topy_gpu_bench.py). A rare
stronghold too big for the GPU buffers (> 512 pieces, ~1 / 6000) comes back as
"overflow" and is computed here on the CPU, so nothing is ever lost.

  g = TopyGpu(mc)                       # raises GpuUnavailable (message) if no GPU
  g.scan(f0, nfam, thr) -> [(fam48, cx, cz, top)]
  g.ring_chunk0(f) -> (cx0, cz0)        # first of the 15 × 15 reachable chunks
"""
from __future__ import annotations

import ctypes as C

import numpy as np

M48 = (1 << 48) - 1
SIDE = 15
PER_FAM = SIDE * SIDE
TG_OVERFLOW = -10000


class GpuUnavailable(RuntimeError):
    pass


class _Pos(C.Structure):
    _fields_ = [("x", C.c_int), ("z", C.c_int)]


_LIB = None


def load():
    """(lib | None, message) — cached by native_libs."""
    global _LIB
    import native_libs
    lib, msg = native_libs.load_cuda("topy_gpu")
    if lib is not None and _LIB is not lib:
        lib.topy_gpu_scan.argtypes = [
            C.c_uint64, C.c_uint32, C.POINTER(C.c_int16), C.POINTER(C.c_int16),
            C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int,
            C.POINTER(C.c_uint32), C.POINTER(C.c_int16), C.POINTER(C.c_int16),
            C.POINTER(C.c_int16), C.c_uint32]
        lib.topy_gpu_scan.restype = C.c_int
        lib.topy_cpu_one.argtypes = [C.c_int64, C.c_int, C.c_int, C.c_int, C.c_int,
                                     C.c_int, C.c_int, C.c_int]
        lib.topy_cpu_one.restype = C.c_int
        if hasattr(lib, "topy_gpu_set_variant"):
            lib.topy_gpu_set_variant.argtypes = [C.c_int]
            lib.topy_gpu_set_variant.restype = C.c_int
        if hasattr(lib, "topy_gpu_stats"):
            lib.topy_gpu_stats.argtypes = [C.POINTER(C.c_int)] * 3 + [C.POINTER(C.c_double)] * 2
            lib.topy_gpu_stats.restype = C.c_int
        _LIB = lib
    return lib, msg


def available() -> bool:
    return load()[0] is not None


def _s64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


class TopyGpu:
    def __init__(self, mc, room=False):
        import cubiomes_wrap
        import stronghold_eyes as se
        lib, msg = load()
        if lib is None:
            raise GpuUnavailable(msg)
        self.lib = lib
        self.mc = int(mc)
        self.sea, self.miny = se._sea_min_for_mc(self.mc)
        self.pre118 = 1 if se._use_old_filler(self.mc) else 0
        if room and not hasattr(lib, "topy_gpu_room"):
            raise GpuUnavailable("topy_gpu.dll predates the portal-room option "
                                 "(rebuild: py tools/build_native.py --unit topy_gpu)")
        # room=True: the top of the PORTAL ROOM (flag read by the generator, 03/10)
        self.inflate = se._inflate_for_mc(self.mc) | (se.TOPY_ROOM if room else 0)
        self.margin = 10
        self.overflows = 0                   # strongholds the GPU handed back to the CPU
        cl = C.CDLL(cubiomes_wrap._find_lib() or cubiomes_wrap._compile_cubiomes())
        self._cub = cl
        if hasattr(cl, "initFirstStronghold"):
            cl.initFirstStronghold.argtypes = [C.c_void_p, C.c_int, C.c_uint64]
            cl.initFirstStronghold.restype = _Pos
            self._ring = lambda f: (lambda p: (p.x, p.z))(cl.initFirstStronghold(None, self.mc, f))
        else:                                # older library: same ring via the bridge
            import ruined_portal as rp
            rp._bind(cl)
            ctx = cl.cb_create()
            cubiomes_wrap.apply_large_ctx(cl, ctx)

            def ring(f):
                cl.cb_setup(ctx, self.mc, f)
                a = [C.c_int(0) for _ in range(4)]
                cl.cb_first_stronghold(ctx, self.mc, f, *[C.byref(v) for v in a])
                return a[0].value, a[1].value
            self._ring = ring
        nat = se._sl_native_init()
        self._glib = nat[2] if nat else None

    # first ring position -> first chunk of the exact 15 × 15 snap range
    # (locateBiome 1.16: 57 × 57 cells of 4 blocks around ring >> 2)
    def ring_chunk0(self, f):
        x, z = self._ring(f & M48)
        return (((x >> 2) - 28) * 4) >> 4, (((z >> 2) - 28) * 4) >> 4

    def cpu_topy(self, f, cx, cz):
        """Exact Top Y on the CPU (gaider_layouts, the reference)."""
        g = self._glib
        if g is None:
            return int(self.lib.topy_cpu_one(_s64(f), cx, cz, self.sea, self.margin,
                                             self.miny, self.inflate, self.pre118))
        s = (C.c_int64 * 1)(_s64(f)); x = (C.c_int32 * 1)(cx); z = (C.c_int32 * 1)(cz)
        os_ = (C.c_int64 * 1)(); oy = (C.c_int32 * 1)()
        g.max_y_batch_arr(s, 1, x, z, self.pre118, self.sea, self.margin, self.miny,
                          self.inflate, -1_000_000, os_, oy)
        return int(oy[0])

    def scan(self, f0, nfam, thr, cx0=None, cz0=None):
        """[(fam48, cx, cz, top)] of the strongholds of families f0 … f0+nfam-1
        with Top Y >= thr. Overflows are recomputed on the CPU (exact)."""
        f0 = int(f0) & M48
        nfam = int(nfam)
        if cx0 is None:
            cx0 = np.empty(nfam, np.int16); cz0 = np.empty(nfam, np.int16)
            for k in range(nfam):
                cx0[k], cz0[k] = self.ring_chunk0((f0 + k) & M48)
        out = []
        self._scan(f0, 0, nfam, int(thr), cx0, cz0, out)
        return out

    def _scan(self, f0, a, n, thr, cx0, cz0, out):
        import native_libs
        cap = max(4096, n * 2) if thr > -1000 else n * PER_FAM
        ofam = np.empty(cap, np.uint32)
        ocx = np.empty(cap, np.int16); ocz = np.empty(cap, np.int16); otop = np.empty(cap, np.int16)
        P16 = C.POINTER(C.c_int16)
        sx = np.ascontiguousarray(cx0[a:a + n]); sz = np.ascontiguousarray(cz0[a:a + n])
        rc = self.lib.topy_gpu_scan(
            (f0 + a) & M48, n, sx.ctypes.data_as(P16), sz.ctypes.data_as(P16),
            thr, self.pre118, self.sea, self.margin, self.miny, self.inflate,
            ofam.ctypes.data_as(C.POINTER(C.c_uint32)), ocx.ctypes.data_as(P16),
            ocz.ctypes.data_as(P16), otop.ctypes.data_as(P16), cap)
        native_libs.check(self.lib, rc, "topy_gpu_scan")
        if rc > cap:                         # too many hits: split the range
            h = n // 2
            self._scan(f0, a, h, thr, cx0, cz0, out)
            self._scan(f0, a + h, n - h, thr, cx0, cz0, out)
            return
        for i in range(rc):
            f = (f0 + a + int(ofam[i])) & M48
            cx, cz, top = int(ocx[i]), int(ocz[i]), int(otop[i])
            if top == TG_OVERFLOW:
                self.overflows += 1
                top = self.cpu_topy(f, cx, cz)
                if top < thr:
                    continue
            out.append((f, cx, cz, top))


VARIANTS = ("8 blocks/SM", "10 blocks/SM", "12 blocks/SM", "6 blocks/SM")


def set_variant(v):
    """Kernel variant (tools/topy_gpu_bench.py measures them); previous one, or None."""
    lib = _LIB
    if lib is None or not hasattr(lib, "topy_gpu_set_variant"):
        return None
    r = lib.topy_gpu_set_variant(int(v))
    return None if r < 0 else r


def stats():
    """{resident, blocks, fam_chunk, last_fam, last_ms} of the GPU scan, or {}."""
    lib = _LIB
    if lib is None or not hasattr(lib, "topy_gpu_stats"):
        return {}
    i = [C.c_int() for _ in range(3)]; d = [C.c_double() for _ in range(2)]
    if lib.topy_gpu_stats(*[C.byref(v) for v in i + d]) != 0:
        return {}
    return dict(resident=i[0].value, blocks=i[1].value, fam_chunk=i[2].value,
                last_fam=d[0].value, last_ms=d[1].value)
