"""
seedlist_io.py - streaming read / count / convert for seed lists.

On-disk formats
---------------
  - plain text  : one signed 64-bit int per line ('#' comments + blanks skipped).
  - ".seeds" (SSA2, compact) : the list is sorted, delta-encoded, then zstd
        (gzip fallback) compressed.
        header  = b"SSA2" + int64 LE count + int64 LE first
        payload = compressed stream of (count - 1) uint64 LE deltas

Why this format: for N distinct 64-bit seeds the information floor is about
log2(2^64 / N) + 1.44 bits each (~40 bits for 43M seeds), so a giant 12-eye list
that is ~880 MB as text stores in ~250 MB and CANNOT go much lower without
losing seeds. The win for SSA is the LAZY reading: iter_seedlist() streams the
list in blocks, so a 43M list never becomes 43M Python ints at once.

Public API
----------
  is_seeds_file(path)            -> bool
  count_seedlist(path)           -> int        (instant for .seeds via header)
  iter_seedlist(path)            -> generator of int seeds (auto text / .seeds)
  convert_to_seeds(in, out)      -> (count, out_bytes)
"""
import os
import struct

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import zstandard as _zstd
    _HAS_ZSTD = True
except ImportError:
    _HAS_ZSTD = False

import gzip

_MAGIC = b"SSA2"
_MASK = (1 << 64) - 1
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
_GZIP_MAGIC = b"\x1f\x8b"


def is_seeds_file(path: str) -> bool:
    """True if `path` is a compact SSA2 .seeds file (by magic, not extension)."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == _MAGIC
    except OSError:
        return False


def count_seedlist(path: str) -> int:
    """Number of seeds. Instant for .seeds (read from header); fast newline
    scan for a text file (no parsing, no full load)."""
    if is_seeds_file(path):
        with open(path, "rb") as f:
            f.read(4)
            return struct.unpack("<q", f.read(8))[0]
    n = 0
    with open(path, "rb") as f:
        for line in f:                 # only lines that start like a number
            t = line.lstrip()
            if t[:1].isdigit() or (t[:1] == b"-" and t[1:2].isdigit()):
                n += 1
    return n


# ── .seeds decode ─────────────────────────────────────────────────────────────
def _decompress(payload: bytes, n: int) -> bytes:
    want = max(0, (n - 1)) * 8
    if payload[:4] == _ZSTD_MAGIC:
        if not _HAS_ZSTD:
            raise RuntimeError(
                "This .seeds is zstd-compressed but the 'zstandard' package is "
                "missing. Install it with:  pip install zstandard")
        return _zstd.ZstdDecompressor().decompress(payload, max_output_size=want)
    if payload[:2] == _GZIP_MAGIC:
        return gzip.decompress(payload)
    raise ValueError("unrecognised .seeds payload compression")


def _decode_seeds_array(path: str):
    """Decode a .seeds file into a sorted int64 array (numpy) or list."""
    with open(path, "rb") as f:
        if f.read(4) != _MAGIC:
            raise ValueError("not a SSA2 .seeds file")
        n = struct.unpack("<q", f.read(8))[0]
        first = struct.unpack("<q", f.read(8))[0]
        payload = f.read()
    raw = _decompress(payload, n)
    if _HAS_NUMPY:
        deltas = np.frombuffer(raw, dtype=np.uint64)
        arr = np.empty(n, dtype=np.int64)
        arr[0] = first
        if n > 1:
            # accumulate mod 2^64 (uint64), then reinterpret to signed int64 so
            # the full signed range round-trips exactly, even huge gaps.
            cs = np.cumsum(deltas, dtype=np.uint64)
            arr[1:] = (cs + np.uint64(first & _MASK)).astype(np.int64)
        return arr
    # pure-python fallback
    out = [first]
    acc = first
    for k in range(0, len(raw), 8):
        acc += struct.unpack_from("<Q", raw, k)[0]
        v = acc & _MASK
        out.append(v - (1 << 64) if v >= (1 << 63) else v)
    return out


def iter_seedlist(path: str, block: int = 1_000_000):
    """Lazy generator of seeds (ints). Auto-detects .seeds vs text. The list is
    yielded in `block`-sized slices internally so memory stays bounded."""
    if is_seeds_file(path):
        arr = _decode_seeds_array(path)
        if _HAS_NUMPY:
            n = len(arr)
            for i in range(0, n, block):
                yield from arr[i:i + block].tolist()
        else:
            yield from arr
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                try:
                    v = int(s.split()[0].rstrip(",;")) & _MASK
                except (ValueError, IndexError):
                    continue
                yield v - (1 << 64) if v >= (1 << 63) else v


class SeedListSource:
    """A file-backed seed list that is len()-able and (re)iterable WITHOUT
    materialising all seeds. Drop-in for a Python list in the search:
      - len(src)  -> seed count (instant for .seeds, fast scan for text)
      - iter(src) -> a FRESH lazy generator of int seeds each time

    Used so a 43M-seed list passed to the search never becomes 43M Python ints.
    """
    __slots__ = ("path", "_count")

    def __init__(self, path: str, count: int = None):
        self.path = path
        self._count = count if count is not None else count_seedlist(path)

    def __len__(self):
        return self._count

    def __iter__(self):
        return iter_seedlist(self.path)


# ── text -> .seeds conversion ─────────────────────────────────────────────────
def _compress_bytes(data: bytes, level: int = 19) -> bytes:
    if _HAS_ZSTD:
        return _zstd.ZstdCompressor(level=level, threads=-1).compress(data)
    return gzip.compress(data, compresslevel=9)


class ConvertCancelled(Exception):
    pass


def _parse_text_block(chunk: bytes):
    """int64 array of the seeds in a block of whole lines. Fast path: one plain
    integer per line (numpy conversion); otherwise line by line, like
    iter_seedlist (first token, unsigned wrapped, junk / comments skipped)."""
    if _HAS_NUMPY and b"#" not in chunk:
        toks = chunk.split()
        nonempty = sum(1 for ln in chunk.splitlines() if ln.strip())
        if len(toks) == nonempty:
            try:
                return np.array(toks).astype(np.int64), 0
            except (ValueError, OverflowError):
                pass
    out, bad = [], 0
    for line in chunk.splitlines():
        t = line.strip()
        if not t or t.startswith(b"#"):
            continue
        try:
            v = int(t.split()[0].rstrip(b",;")) & _MASK
        except (ValueError, IndexError):
            bad += 1
            continue
        out.append(v - (1 << 64) if v >= (1 << 63) else v)
    if _HAS_NUMPY:
        return np.array(out, dtype=np.int64), bad
    return out, bad


def convert_to_seeds(in_path: str, out_path: str, progress=None, on_frac=None,
                     cancel=None, stats=None, dedupe=True):
    """Read a text seedlist and write a compact .seeds. Returns (count, bytes).
    `progress(str)`: short status; `on_frac(0..1)`: reading progress;
    `cancel`: a threading.Event (set → ConvertCancelled, no output left);
    `stats`: dict filled with lines_bad / duplicates / in_bytes.
    Streams the input in 8 MB blocks into int64 arrays (8 bytes per seed —
    an 800 MB list no longer becomes 40 M Python ints)."""
    if progress:
        progress("reading")
    total = max(1, os.path.getsize(in_path))
    parts, bad, done = [], 0, 0
    with open(in_path, "rb") as f:
        rest = b""
        while True:
            if cancel is not None and cancel.is_set():
                raise ConvertCancelled()
            b = f.read(8 << 20)
            if not b:
                chunk, rest = rest, b""
            else:
                done += len(b)
                buf = rest + b
                cut = buf.rfind(b"\n")
                if cut < 0:
                    rest = buf
                    continue
                chunk, rest = buf[:cut + 1], buf[cut + 1:]
            if chunk:
                arr, nb = _parse_text_block(chunk)
                bad += nb
                if len(arr):
                    parts.append(arr)
            if on_frac:
                on_frac(min(1.0, done / total))
            if not b:
                break
    if _HAS_NUMPY:
        arr = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int64)
        del parts
        n_in = len(arr)
        if progress:
            progress(f"sorting {n_in:,}")
        arr.sort()                            # (np.unique is ~60× slower here)
        if dedupe and len(arr) > 1:
            keep = np.empty(len(arr), dtype=bool); keep[0] = True
            np.not_equal(arr[1:], arr[:-1], out=keep[1:])
            if not keep.all():
                arr = arr[keep]
    else:
        seeds = [x for p_ in parts for x in p_]
        n_in = len(seeds)
        seeds = sorted(set(seeds)) if dedupe else sorted(seeds)
    n = len(arr) if _HAS_NUMPY else len(seeds)
    if stats is not None:
        stats.update(lines_bad=bad, duplicates=n_in - n, in_bytes=total)
    if n == 0:
        raise ValueError("no seeds found in input")
    if cancel is not None and cancel.is_set():
        raise ConvertCancelled()
    if _HAS_NUMPY:
        first = int(arr[0])
        with np.errstate(over="ignore"):
            raw = np.diff(arr).view(np.uint64).tobytes()
        del arr
    else:
        first = seeds[0]
        raw = b"".join(struct.pack("<Q", (seeds[i] - seeds[i - 1]) & _MASK)
                       for i in range(1, n))
        del seeds
    if progress:
        progress("compressing")
    comp = _compress_bytes(raw)
    del raw
    if cancel is not None and cancel.is_set():
        raise ConvertCancelled()
    header = _MAGIC + struct.pack("<q", n) + struct.pack("<q", first)
    tmp = out_path + ".part"
    with open(tmp, "wb") as f:
        f.write(header)
        f.write(comp)
    os.replace(tmp, out_path)                 # never a half-written .seeds
    return n, os.path.getsize(out_path)
