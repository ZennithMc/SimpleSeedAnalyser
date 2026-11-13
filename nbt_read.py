"""nbt_read.py — minimal (gzipped) NBT reader for structure templates (24/10)."""
import gzip
import struct


def _rd(b, i, t):
    if t == 1:
        return struct.unpack_from(">b", b, i)[0], i + 1
    if t == 2:
        return struct.unpack_from(">h", b, i)[0], i + 2
    if t == 3:
        return struct.unpack_from(">i", b, i)[0], i + 4
    if t == 4:
        return struct.unpack_from(">q", b, i)[0], i + 8
    if t == 5:
        return struct.unpack_from(">f", b, i)[0], i + 4
    if t == 6:
        return struct.unpack_from(">d", b, i)[0], i + 8
    if t == 7:
        n = struct.unpack_from(">i", b, i)[0]
        return bytes(b[i + 4:i + 4 + n]), i + 4 + n
    if t == 8:
        n = struct.unpack_from(">H", b, i)[0]
        return b[i + 2:i + 2 + n].decode("utf-8", "replace"), i + 2 + n
    if t == 9:
        et = b[i]
        n = struct.unpack_from(">i", b, i + 1)[0]
        i += 5
        out = []
        for _ in range(n):
            v, i = _rd(b, i, et)
            out.append(v)
        return out, i
    if t == 10:
        d = {}
        while True:
            tt = b[i]
            i += 1
            if tt == 0:
                return d, i
            n = struct.unpack_from(">H", b, i)[0]
            name = b[i + 2:i + 2 + n].decode("utf-8", "replace")
            i += 2 + n
            d[name], i = _rd(b, i, tt)
    if t == 11:
        n = struct.unpack_from(">i", b, i)[0]
        return list(struct.unpack_from(">%di" % n, b, i + 4)), i + 4 + 4 * n
    if t == 12:
        n = struct.unpack_from(">i", b, i)[0]
        return list(struct.unpack_from(">%dq" % n, b, i + 4)), i + 4 + 8 * n
    raise ValueError(f"tag {t}")


def load(data: bytes):
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    t = data[0]
    n = struct.unpack_from(">H", data, 1)[0]
    v, _ = _rd(data, 3 + n, t)
    return v


def block_state(st):
    """A palette entry as {"Name", "Properties"} whatever the format (26.3 writes
    {"id", "properties"}, {"": name} or "name[k=v]")."""
    if isinstance(st, str):
        if "[" in st:
            n, rest = st.split("[", 1)
            return {"Name": n, "Properties": dict(kv.split("=", 1) for kv in rest.rstrip("]").split(",") if "=" in kv)}
        return {"Name": st}
    if "Name" in st:
        return st
    if "id" in st:
        return {"Name": st["id"], "Properties": st.get("properties", {})}
    if "" in st:
        return {"Name": st[""], "Properties": {k: v for k, v in st.items() if k}}
    return st
