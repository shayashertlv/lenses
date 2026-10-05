"""Minimal, strict GLB (glTF 2.0 binary) reader for host-side checks.

Reads the JSON and BIN chunks, decodes accessors (strided, normalized and sparse), composes node world transforms and
returns every mesh primitive with its world-space positions. No third-party glTF library: numpy only.

- TRIANGLES, TRIANGLE_STRIP and TRIANGLE_FAN primitives all come back as a triangle list (`Primitive.triangles`).
- Compressed geometry (Draco, meshopt) is reported, not decoded: `Primitive.compressed` names the extension, the
  arrays are empty, and `Primitive.bounds` is its world-space box from the POSITION accessor's min/max (which glTF
  requires and Draco files keep), so validators can still bound what the undecoded geometry could change.
- Index accessors must be unsigned SCALAR integers (5121/5123/5125) with no negative value; anything else is recorded in
  `Primitive.index_issue` (numpy would silently wrap a negative index to the last vertex).
- A primitive whose data cannot be decoded (a missing buffer, an accessor running past its view, ...) does not stop
  the load: `Primitive.error` says why and its arrays are empty.
- External resources are resolved next to the file; missing ones are listed in `Glb.missing_uris`.
- A document without a usable default scene falls back to the root nodes and says so in `Glb.scene_issue`.
The node tree is walked iteratively (legal glTF hierarchies can be thousands of levels deep).
"""
from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import numpy as np

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
COMPONENT_DTYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
TYPE_SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}
COMPRESSION_EXTENSIONS = ("KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_meshopt_compression")
TRIANGLE_MODES = (4, 5, 6)          # TRIANGLES, TRIANGLE_STRIP, TRIANGLE_FAN
INDEX_COMPONENT_TYPES = (5121, 5123, 5125)   # glTF indices: UNSIGNED_BYTE, UNSIGNED_SHORT, UNSIGNED_INT


class GlbError(ValueError):
    """The file is not a readable GLB."""


@dataclass
class Primitive:
    node_index: int
    node_name: str
    mesh_index: int
    mesh_name: str
    primitive_index: int
    material_index: int | None
    mode: int
    positions: np.ndarray            # (N, 3) float64, WORLD space (node transforms applied)
    triangles: np.ndarray            # (M, 3) int64 indices into positions; strips and fans are triangulated
    normals: np.ndarray | None       # (N, 3) float64, local space as stored
    uv0: np.ndarray | None           # (N, 2) float64
    world: np.ndarray                # (4, 4) node world matrix
    raw_index_count: int             # number of indices (or vertices when unindexed) before triangulation
    max_index: int                   # largest index value (-1 when unindexed)
    has_morph_targets: bool
    compressed: str | None = None    # compression extension name when geometry was not decoded
    error: str | None = None         # why the primitive's data could not be decoded (arrays are then empty)
    attributes: tuple[str, ...] = ()  # attribute semantics present (POSITION, NORMAL, TEXCOORD_0, ...)
    index_issue: str | None = None   # why the index accessor is not valid glTF (signed / non-scalar / negative ...)
    bounds: np.ndarray | None = None  # (2, 3) world-space box of a COMPRESSED primitive (POSITION min/max), else None


@dataclass
class Glb:
    path: Path
    doc: dict
    bin_chunk: bytes
    file_bytes: int
    primitives: list[Primitive] = field(default_factory=list)
    external_uris: list[str] = field(default_factory=list)
    missing_uris: list[str] = field(default_factory=list)     # "buffer 1: missing.bin", "image 0: tex.png"
    scene_issue: str | None = None                             # no scenes / bad default scene / empty default scene

    @property
    def materials(self) -> list[dict]:
        return self.doc.get("materials", [])

    @property
    def nodes(self) -> list[dict]:
        return self.doc.get("nodes", [])

    @property
    def meshes(self) -> list[dict]:
        return self.doc.get("meshes", [])

    def material(self, index: int | None) -> dict | None:
        """Material `index`, or None when the index is None or out of range."""
        mats = self.materials
        if isinstance(index, int) and 0 <= index < len(mats) and isinstance(mats[index], dict):
            return mats[index]
        return None

    def resolve(self, uri: str) -> Path:
        return self.path.parent / unquote(uri)

    def image_bytes(self, image_index: int) -> bytes | None:
        """The image's encoded bytes; None when its external file is missing. Raises GlbError on a broken view."""
        img = self.doc.get("images", [])[image_index]
        if "bufferView" in img:
            return bytes(self._view_bytes(img["bufferView"]))
        uri = img.get("uri", "")
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        p = self.resolve(uri)
        return p.read_bytes() if uri and p.is_file() else None

    # -- internals ---------------------------------------------------------------------------------------------
    def _buffer(self, index: int) -> bytes:
        buffers = self.doc.get("buffers", [])
        if not (isinstance(index, int) and 0 <= index < len(buffers)):
            raise GlbError(f"buffer {index} does not exist")
        buf = buffers[index]
        uri = buf.get("uri")
        if uri is None:
            if index != 0:
                raise GlbError(f"buffer {index} has no uri and is not the GLB BIN chunk")
            return self.bin_chunk
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        p = self.resolve(uri)
        if not p.is_file():
            raise GlbError(f"external buffer {uri!r} not found")
        return p.read_bytes()

    def _view_bytes(self, view_index: int) -> memoryview:
        views = self.doc.get("bufferViews", [])
        if not (isinstance(view_index, int) and 0 <= view_index < len(views)):
            raise GlbError(f"bufferView {view_index} does not exist")
        view = views[view_index]
        data = self._buffer(view["buffer"])
        start = view.get("byteOffset", 0)
        end = start + view["byteLength"]
        if end > len(data):
            raise GlbError(f"bufferView {view_index} runs past the end of its buffer")
        return memoryview(data)[start:end]

    def accessor(self, index: int) -> np.ndarray:
        """Decode accessor `index` into an (count, ncomp) array (float64 when normalized or float)."""
        accessors = self.doc.get("accessors", [])
        if not (isinstance(index, int) and 0 <= index < len(accessors)):
            raise GlbError(f"accessor {index} does not exist")
        acc = accessors[index]
        dtype = np.dtype(COMPONENT_DTYPES[acc["componentType"]]).newbyteorder("<")
        ncomp = TYPE_SIZES[acc["type"]]
        count = acc["count"]
        if "bufferView" in acc:
            view = self.doc["bufferViews"][acc["bufferView"]]
            raw = self._view_bytes(acc["bufferView"])
            offset = acc.get("byteOffset", 0)
            item = dtype.itemsize * ncomp
            stride = view.get("byteStride") or item
            need = offset + stride * (count - 1) + item if count else 0
            if need > len(raw):
                raise GlbError(f"accessor {index} runs past the end of its bufferView")
            if stride == item:
                arr = np.frombuffer(raw, dtype=dtype, count=count * ncomp, offset=offset).reshape(count, ncomp)
            else:
                rows = np.frombuffer(raw, dtype=np.uint8, count=need - offset, offset=offset)
                rows = np.lib.stride_tricks.as_strided(rows, shape=(count, item), strides=(stride, 1))
                arr = np.frombuffer(rows.copy().tobytes(), dtype=dtype).reshape(count, ncomp)
            arr = arr.copy()
        else:
            arr = np.zeros((count, ncomp), dtype=dtype)
        if "sparse" in acc:
            sp = acc["sparse"]
            idt = np.dtype(COMPONENT_DTYPES[sp["indices"]["componentType"]]).newbyteorder("<")
            iv = self._view_bytes(sp["indices"]["bufferView"])
            idx = np.frombuffer(iv, dtype=idt, count=sp["count"], offset=sp["indices"].get("byteOffset", 0))
            vv = self._view_bytes(sp["values"]["bufferView"])
            vals = np.frombuffer(vv, dtype=dtype, count=sp["count"] * ncomp,
                                 offset=sp["values"].get("byteOffset", 0)).reshape(-1, ncomp)
            idx = idx.astype(np.int64)
            if len(idx) and (idx.min() < 0 or idx.max() >= count):
                raise GlbError(f"accessor {index}: sparse index out of range")
            arr[idx] = vals
        if acc.get("normalized"):
            info = np.iinfo(dtype)
            out = arr.astype(np.float64) / info.max
            return np.maximum(out, -1.0) if info.min < 0 else out
        return arr.astype(np.float64) if dtype.kind == "f" else arr.astype(np.int64)


def node_local_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T     # glTF stores column-major
    x, y, z, w = node.get("rotation", [0.0, 0.0, 0.0, 1.0])
    sx, sy, sz = node.get("scale", [1.0, 1.0, 1.0])
    rot = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    m = np.eye(4)
    m[:3, :3] = rot * np.array([sx, sy, sz])[None, :]
    m[:3, 3] = node.get("translation", [0.0, 0.0, 0.0])
    return m


def split_chunks(data: bytes) -> tuple[dict, bytes]:
    if len(data) < 20 or data[:4] != GLB_MAGIC:
        raise GlbError("not a GLB file (missing 'glTF' magic)")
    version, length = struct.unpack("<II", data[4:12])
    if version != 2:
        raise GlbError(f"GLB version {version}, expected 2")
    if length != len(data):
        raise GlbError(f"GLB header length {length} != file size {len(data)}")
    off, doc, bin_chunk = 12, None, b""
    while off + 8 <= len(data):
        clen, ctype = struct.unpack("<II", data[off:off + 8])
        body = data[off + 8:off + 8 + clen]
        if len(body) != clen:
            raise GlbError("truncated GLB chunk")
        if ctype == CHUNK_JSON and doc is None:
            doc = json.loads(body.decode("utf-8"))
        elif ctype == CHUNK_BIN and not bin_chunk:
            bin_chunk = bytes(body)
        off += 8 + clen
    if doc is None:
        raise GlbError("GLB has no JSON chunk")
    if not isinstance(doc, dict):
        raise GlbError("GLB JSON chunk is not an object")
    return doc, bin_chunk


def triangulate(idx: np.ndarray, mode: int) -> np.ndarray:
    """(M, 3) triangle list from an index list in glTF primitive `mode` (4, 5 or 6; anything else gives none).
    Strips alternate the winding as glTF 2.0 §3.7.2.1 specifies; fans pivot on the first index."""
    n = len(idx)
    if mode == 4:
        return idx[: n // 3 * 3].reshape(-1, 3)
    if mode in (5, 6) and n >= 3:
        i = np.arange(n - 2)
        if mode == 5:
            return np.stack([idx[i], idx[i + 1 + (i % 2)], idx[i + 2 - (i % 2)]], axis=1)
        return np.stack([idx[i + 1], idx[i + 2], np.full(n - 2, idx[0])], axis=1)
    return np.zeros((0, 3), np.int64)


def load(path: str | Path) -> Glb:
    path = Path(path)
    data = path.read_bytes()
    doc, bin_chunk = split_chunks(data)
    glb = Glb(path=path, doc=doc, bin_chunk=bin_chunk, file_bytes=len(data))
    for kind, items in (("buffer", doc.get("buffers", [])), ("image", doc.get("images", []))):
        for i, item in enumerate(items):
            uri = item.get("uri") if isinstance(item, dict) else None
            if uri and not uri.startswith("data:"):
                glb.external_uris.append(uri)
                if not glb.resolve(uri).is_file():
                    glb.missing_uris.append(f"{kind} {i}: {uri}")

    nodes = doc.get("nodes", [])
    scenes = doc.get("scenes")
    roots: list = []
    if not scenes:
        glb.scene_issue = "the document has no scenes (glTF viewers show nothing)"
        children = {c for n in nodes for c in n.get("children", [])}
        roots = [i for i in range(len(nodes)) if i not in children]
    else:
        si = doc.get("scene", 0)
        if not (isinstance(si, int) and 0 <= si < len(scenes)):
            glb.scene_issue = f"default scene {si!r} does not exist ({len(scenes)} scene(s))"
        else:
            roots = list(scenes[si].get("nodes", []))
            if not roots:
                glb.scene_issue = f"the default scene ({si}) has no nodes"

    seen: set[int] = set()
    stack = [(r, np.eye(4)) for r in reversed(roots)]
    while stack:                                            # iterative depth-first walk, children in order
        ni, parent = stack.pop()
        if not (isinstance(ni, int) and 0 <= ni < len(nodes)):
            raise GlbError(f"node index {ni!r} does not exist")
        if ni in seen:
            raise GlbError(f"node {ni} is reachable twice (cycle or shared child)")
        seen.add(ni)
        node = nodes[ni]
        world = parent @ node_local_matrix(node)
        if "mesh" in node:
            mi = node["mesh"]
            if not (isinstance(mi, int) and 0 <= mi < len(doc.get("meshes", []))):
                raise GlbError(f"node {ni} references mesh {mi!r}, which does not exist")
            mesh = doc["meshes"][mi]
            for pi, prim in enumerate(mesh.get("primitives", [])):
                glb.primitives.append(_primitive(glb, ni, node, mi, mesh, pi, prim, world))
        for c in reversed(node.get("children", [])):
            stack.append((c, world))
    return glb


def _empty(**common) -> Primitive:
    return Primitive(positions=np.zeros((0, 3)), triangles=np.zeros((0, 3), np.int64), normals=None, uv0=None,
                     raw_index_count=0, max_index=-1, **common)


def accessor_bounds(glb: Glb, index, world: np.ndarray) -> np.ndarray | None:
    """(2, 3) world-space box of a POSITION accessor from its JSON min/max (no data decoded), or None when the accessor
    has no usable min/max. The 8 corners are transformed, so the box is exact for an identity world matrix and
    conservative otherwise."""
    try:
        acc = glb.doc["accessors"][index]
        lo, hi = acc.get("min"), acc.get("max")
        if not (isinstance(lo, list) and isinstance(hi, list) and len(lo) >= 3 and len(hi) >= 3):
            return None
        lo, hi = np.asarray(lo[:3], np.float64), np.asarray(hi[:3], np.float64)
        if not (np.isfinite(lo).all() and np.isfinite(hi).all()) or (lo > hi).any():
            return None
        dtype = np.dtype(COMPONENT_DTYPES.get(acc.get("componentType"), np.float32))
        if acc.get("normalized") and dtype.kind in "iu" and max(np.abs(lo).max(), np.abs(hi).max()) > 1.0:
            info = np.iinfo(dtype)                              # bounds stored as the raw integers
            lo, hi = np.maximum(lo / info.max, -1.0), np.maximum(hi / info.max, -1.0)
        corners = np.array([[x, y, z, 1.0] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        w = (corners @ np.asarray(world, np.float64).T)[:, :3]
        return np.stack([w.min(0), w.max(0)])
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def _index_issue(acc: dict, idx: np.ndarray) -> str | None:
    issues = []
    ct = acc.get("componentType")
    if ct not in INDEX_COMPONENT_TYPES:
        issues.append(f"indices componentType {ct} is not valid glTF (indices are unsigned: 5121, 5123 or 5125)")
    if acc.get("type") != "SCALAR":
        issues.append(f"indices accessor type {acc.get('type')!r} is not SCALAR")
    if acc.get("normalized"):
        issues.append("indices accessor is normalized")
    if len(idx) and int(idx.min()) < 0:
        issues.append(f"index {int(idx.min())} < 0")
    return "; ".join(issues) or None


def _primitive(glb: Glb, ni: int, node: dict, mi: int, mesh: dict, pi: int, prim: dict, world: np.ndarray) -> Primitive:
    mode = prim.get("mode", 4)
    attrs = prim.get("attributes", {}) or {}
    common = dict(node_index=ni, node_name=node.get("name", f"node{ni}"), mesh_index=mi,
                  mesh_name=mesh.get("name", f"mesh{mi}"), primitive_index=pi, material_index=prim.get("material"),
                  mode=mode, world=world, has_morph_targets=bool(prim.get("targets")),
                  attributes=tuple(sorted(attrs)))
    try:
        compressed = next((e for e in COMPRESSION_EXTENSIONS if e in (prim.get("extensions") or {})), None)
        if "POSITION" not in attrs:
            if compressed:
                return _empty(compressed=compressed, **common)
            raise GlbError("no POSITION attribute")
        accessors, pa = glb.doc.get("accessors", []), attrs["POSITION"]
        if not (isinstance(pa, int) and 0 <= pa < len(accessors)):
            raise GlbError(f"POSITION accessor {pa!r} does not exist")
        if compressed is None and "bufferView" in accessors[pa]:
            view = glb.doc["bufferViews"][accessors[pa]["bufferView"]]
            compressed = next((e for e in COMPRESSION_EXTENSIONS if e in (view.get("extensions") or {})), None)
        if compressed:
            return _empty(compressed=compressed, bounds=accessor_bounds(glb, pa, world), **common)
        local = glb.accessor(attrs["POSITION"])[:, :3]
        pos = (np.c_[local, np.ones(len(local))] @ world.T)[:, :3]
        index_issue = None
        if "indices" in prim:
            idx = glb.accessor(prim["indices"]).reshape(-1)
            index_issue = _index_issue(accessors[prim["indices"]], idx)
            idx = idx.astype(np.int64)                          # a (rejected) normalized accessor decodes to floats
            max_index = int(idx.max()) if len(idx) else -1
        else:
            idx = np.arange(len(local), dtype=np.int64)
            max_index = -1
        tris = triangulate(idx, mode)
        normals = glb.accessor(attrs["NORMAL"]) if "NORMAL" in attrs else None
        uv0 = glb.accessor(attrs["TEXCOORD_0"]) if "TEXCOORD_0" in attrs else None
    except (GlbError, KeyError, IndexError, ValueError, TypeError) as exc:
        return _empty(error=f"{type(exc).__name__}: {exc}", **common)
    return Primitive(positions=pos, triangles=tris, normals=normals, uv0=uv0, raw_index_count=len(idx),
                     max_index=max_index, index_issue=index_issue, **common)


def image_size(data: bytes | None) -> tuple[int, int] | None:
    """(width, height) from PNG, JPEG or WebP (VP8, VP8L, VP8X) header bytes, or None if unrecognised."""
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            w, h = struct.unpack("<HH", data[26:30])
            return int(w & 0x3FFF), int(h & 0x3FFF)
        if chunk == b"VP8L" and data[20] == 0x2F:
            b = int.from_bytes(data[21:25], "little")
            return int((b & 0x3FFF) + 1), int(((b >> 14) & 0x3FFF) + 1)
        if chunk == b"VP8X":
            return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
        return None
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return int(w), int(h)
            i += 2 + seg
    return None
