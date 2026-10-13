"""Server-side port of the game's network/nex_prot.py wire format (Python 3).

Message on the wire:  u16 total_len (incl. these 2 bytes) | u16 cmd | u8 subcmd | body
Body: sequence of (tag, value). tag = 1 byte (wire<<5 | idx) for idx<=15, else 2 bytes.
Protocol tables are loaded from the decompiled proto/*.py files.
"""
import json
import os
import re
import struct

WIRE_8B, WIRE_16B, WIRE_32B, WIRE_VARINT, WIRE_VARLEN, WIRE_FVEC = 1, 2, 3, 4, 5, 6
NEX_TO_WIRE = {
    "INT8": WIRE_8B, "UINT8": WIRE_8B, "INT16": WIRE_16B, "UINT16": WIRE_16B,
    "INT32": WIRE_32B, "UINT32": WIRE_32B, "VARINT": WIRE_VARINT, "VARSTR": WIRE_VARLEN,
    "ARRAY": WIRE_VARLEN, "BINBUFF": WIRE_VARLEN, "OPTIONAL": WIRE_VARLEN, "FLOAT": WIRE_32B,
    "FVEC3": WIRE_FVEC,
}
FIXED = {"INT8": "<b", "UINT8": "<B", "INT16": "<h", "UINT16": "<H", "INT32": "<i",
         "UINT32": "<I", "FLOAT": "<f"}
WIRE_SIZE = {WIRE_8B: 1, WIRE_16B: 2, WIRE_32B: 4, WIRE_FVEC: 12}


def gen_tag(idx, nex_type):
    wire = NEX_TO_WIRE[nex_type]
    if idx <= 15:
        return (wire & 7) << 5 | idx
    return (wire & 7) << 13 | idx | 4096


def pack_tag(tag):
    if tag & 0xFF00:
        return struct.pack("<BB", tag >> 8 & 255, tag & 255)
    return struct.pack("<B", tag & 255)


def unpack_tag(buf, off):
    fb = buf[off]
    off += 1
    if fb & 16:
        return fb << 8 | buf[off], off + 1
    return fb, off


def wire_of_tag(tag):
    return tag >> 13 if tag & 0xFF00 else tag >> 5


def trailing_zeros8(v):
    n = 0
    while n < 8 and not (v >> n) & 1:
        n += 1
    return n


def skip_len(tag, buf, off):
    wire = wire_of_tag(tag)
    if wire in WIRE_SIZE:
        return WIRE_SIZE[wire]
    if wire == WIRE_VARINT:
        p = buf[off]
        if p & 1:
            return 1
        return 9 if p == 0 else trailing_zeros8(p) + 1
    if wire == WIRE_VARLEN:
        return struct.unpack_from("<H", buf, off)[0] + 2
    raise ValueError("bad wire type %d" % wire)


# ---------------------------------------------------------------- varint (zigzag, prefix-length)
def pack_varint(v):
    v = (v << 1) ^ (v >> 63)
    v &= (1 << 64) - 1
    bits = max(1, v.bit_length())
    n = 1 + (bits - 1) // 7
    if bits > 56:
        return struct.pack("<BQ", 0, v)
    v = (2 * v + 1) << (n - 1)
    return bytes((v >> (8 * i)) & 255 for i in range(n))


def unpack_varint(buf, off):
    p = buf[off]
    off += 1
    if p & 1:
        ret = p >> 1
    else:
        n = 9 if p == 0 else trailing_zeros8(p) + 1
        if n < 9:
            ret = 0
            for i in range(1, n):
                ret |= buf[off] << (i * 8)
                off += 1
            ret |= p
            ret >>= n
        else:
            ret = struct.unpack_from("<Q", buf, off)[0]
            off += 8
    return (ret >> 1) ^ -(ret & 1), off


# ---------------------------------------------------------------- values
def to_bytes(v):
    if isinstance(v, bytes):
        return v
    if isinstance(v, str):
        return v.encode("utf-8")
    return str(v).encode("utf-8")


def pack_value(nex_type, extra, v):
    if nex_type in FIXED:
        return struct.pack(FIXED[nex_type], v)
    if nex_type == "VARINT":
        return pack_varint(int(v))
    if nex_type == "FVEC3":
        return struct.pack("<fff", *v)
    if nex_type in ("VARSTR", "BINBUFF"):
        b = to_bytes(v)
        return struct.pack("<H", len(b)) + b
    if nex_type == "ARRAY":
        sub = extra[0]
        out = b""
        for item in v:
            if isinstance(sub, tuple):
                # missing element fields are filled with defaults by the client (ArrayValue.unpack)
                ele = pack_fields(sub, item, all_fields=False)
                out += struct.pack("<H", len(ele)) + ele
            else:
                out += pack_value(sub, (), item)
        return struct.pack("<H", len(out)) + out
    if nex_type == "OPTIONAL":
        sub = extra[0]
        ele = pack_fields(sub, v or {}, all_fields=False)
        return struct.pack("<H", len(ele)) + ele
    raise ValueError("unknown type %s" % nex_type)


DEFAULTS = {"VARSTR": b"", "BINBUFF": b"", "ARRAY": [], "OPTIONAL": {}, "FVEC3": (0.0, 0.0, 0.0),
            "FLOAT": 0.0}


def pack_fields(desc, data, all_fields):
    out = b""
    for d in desc:
        idx, nex_type, name = d[0], d[1], d[2]
        if name in data and data[name] is not None:
            v = data[name]
        elif all_fields:
            v = DEFAULTS.get(nex_type, 0)
        else:
            continue
        out += pack_tag(gen_tag(idx, nex_type)) + pack_value(nex_type, d[3:], v)
    return out


def unpack_value(nex_type, extra, buf, off):
    if nex_type in FIXED:
        fmt = FIXED[nex_type]
        return struct.unpack_from(fmt, buf, off)[0], off + struct.calcsize(fmt)
    if nex_type == "VARINT":
        return unpack_varint(buf, off)
    if nex_type == "FVEC3":
        return struct.unpack_from("<fff", buf, off), off + 12
    if nex_type in ("VARSTR", "BINBUFF"):
        n = struct.unpack_from("<H", buf, off)[0]
        off += 2
        return bytes(buf[off:off + n]), off + n
    if nex_type == "ARRAY":
        n = struct.unpack_from("<H", buf, off)[0]
        off += 2
        end = off + n
        sub = extra[0]
        items = []
        while off < end:
            if isinstance(sub, tuple):
                sl = struct.unpack_from("<H", buf, off)[0]
                off += 2
                items.append(unpack_fields(sub, buf, off, off + sl))
                off += sl
            else:
                v, off = unpack_value(sub, (), buf, off)
                items.append(v)
        return items, end
    if nex_type == "OPTIONAL":
        n = struct.unpack_from("<H", buf, off)[0]
        off += 2
        return unpack_fields(extra[0], buf, off, off + n), off + n
    raise ValueError("unknown type %s" % nex_type)


def unpack_fields(desc, buf, off, end):
    by_tag = {gen_tag(d[0], d[1]): d for d in desc}
    out = {}
    while off < end:
        tag, off = unpack_tag(buf, off)
        d = by_tag.get(tag)
        if d is None:
            off += skip_len(tag, buf, off)
            continue
        out[d[2]], off = unpack_value(d[1], d[3:], buf, off)
    return out


# ---------------------------------------------------------------- protocol tables
def _as_tuples(v):
    """JSON turns the field tuples into lists; the codec relies on tuples for nested structs."""
    return tuple(_as_tuples(x) for x in v) if isinstance(v, list) else v


class Protocol:
    """Message table: from protocol.json (release) or from decompiled proto/*.py (development)."""

    def __init__(self, source):
        self.by_id = {}    # (cmd, subcmd, 'C'|'S') -> definition dict (with 'NAME')
        self.by_name = {}  # 'S_LOGIN_AUTH' -> definition dict
        if source.endswith(".json"):
            with open(source, encoding="utf-8") as f:
                for name, d in json.load(f).items():
                    self._add(name, {"CMD": d["CMD"], "SUBCMD": d["SUBCMD"], "BODY": _as_tuples(d["BODY"])})
            return
        for fn in sorted(os.listdir(source)):
            if not fn.endswith("_pto.py"):
                continue
            src = open(os.path.join(source, fn), encoding="utf-8").read()
            src = re.sub(r"^return\s*$", "", src, flags=re.M)
            ns = {}
            exec(compile(src, fn, "exec"), ns)
            for name, d in ns.items():
                if (name.startswith("S_") or name.startswith("C_")) and isinstance(d, dict) and "SUBCMD" in d:
                    self._add(name, d)

    def _add(self, name, d):
        d = dict(d, NAME=name)
        self.by_id[(d["CMD"], d["SUBCMD"]) + (name[0],)] = d
        self.by_name[name] = d

    def export_json(self, path):
        """Wire layout only (ids, field numbers, types, names): no descriptions or handler code."""
        out = {n: {"CMD": d["CMD"], "SUBCMD": d["SUBCMD"], "BODY": d["BODY"]} for n, d in sorted(self.by_name.items())}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"))

    def encode(self, name, data):
        d = self.by_name[name]
        body = pack_fields(d["BODY"], data, all_fields=False)
        msg = struct.pack("<HB", d["CMD"], d["SUBCMD"]) + body
        return struct.pack("<H", len(msg) + 2) + msg

    def decode(self, msg, kind="C"):
        """msg = cmd/subcmd/body (length prefix already stripped)."""
        cmd, sub = struct.unpack_from("<HB", msg, 0)
        d = self.by_id.get((cmd, sub, kind))
        if d is None:
            return None, cmd, sub, None
        return d["NAME"], cmd, sub, unpack_fields(d["BODY"], msg, 3, len(msg))
