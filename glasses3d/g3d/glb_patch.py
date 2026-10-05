"""Rewrite names inside a GLB's JSON chunk (stdlib only, so it runs in Blender's Python too).

Blender cannot give the temporary export copies their authored names (the originals still own them), so the exporter
writes the copies and then maps every node, mesh and material name back to the authored one here.
"""
from __future__ import annotations

import json
import struct

_JSON, _BIN = 0x4E4F534A, 0x004E4942


def read_chunks(data: bytes) -> tuple[dict, bytes]:
    if data[:4] != b"glTF":
        raise ValueError("not a GLB")
    off, doc, binc = 12, None, b""
    while off + 8 <= len(data):
        ln, typ = struct.unpack("<II", data[off:off + 8])
        body = data[off + 8:off + 8 + ln]
        if typ == _JSON and doc is None:
            doc = json.loads(body.decode("utf-8"))
        elif typ == _BIN and not binc:
            binc = bytes(body)
        off += 8 + ln
    if doc is None:
        raise ValueError("GLB has no JSON chunk")
    return doc, binc


def write_chunks(doc: dict, binc: bytes) -> bytes:
    js = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * (-len(js) % 4)
    out = struct.pack("<II", len(js), _JSON) + js
    if binc:
        binc = binc + b"\0" * (-len(binc) % 4)
        out += struct.pack("<II", len(binc), _BIN) + binc
    return b"glTF" + struct.pack("<II", 2, 12 + len(out)) + out


def rename(data: bytes, *, nodes: dict | None = None, meshes: dict | None = None, materials: dict | None = None) -> tuple[bytes, dict]:
    """Return (new GLB bytes, counts of renamed entries). Names not in a mapping are left alone."""
    doc, binc = read_chunks(data)
    counts = {}
    for key, mapping in (("nodes", nodes), ("meshes", meshes), ("materials", materials)):
        n = 0
        for entry in doc.get(key, []):
            name = entry.get("name")
            if mapping and name in mapping:
                entry["name"] = mapping[name]
                n += 1
        counts[key] = n
    return write_chunks(doc, binc), counts
