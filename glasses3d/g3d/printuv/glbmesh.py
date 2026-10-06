"""The print layout's view of a GLB, shared by Blender (``g3d.print_uv``) and the host (the bake's manifest, the
staleness check). numpy + the stdlib GLB chunk reader only, so it runs in Blender 5.2's Python.

Why this module exists (WP5 step 4, Blender side): the layout core (``g3d.printuv.layout``) is a function of the
triangle arrays it is given, vertex indices included (glTF splits vertices at uv / normal seams, and the strip fit and
the component labels see those splits). The host bakes on the arrays of an exported GLB; so ``g3d.print_uv`` runs the
core on the arrays of the SAME export, made in-process by the WP3 exporter, and the two cannot disagree. A test checks
that this reproduces the host's layout of q001.glb chart for chart.

    read_glb(data)                  -> [Prim]: node name, positions in mm (float64 of the float32 metres x 1000, exactly
                                       the host's ``fitmesh.load_fit_mesh``), triangles, material, every TEXCOORD_n
    mesh_arrays(prims)              -> (V, F, part, names) of every triangle primitive in node order (the host's order)
    geometry_sha(V, F, part, names, parts)
                                    -> the print geometry's identity: per part (in ``parts`` order) its triangles with
                                       corners rotated so the smallest corner comes first (winding kept) and sorted;
                                       independent of vertex splitting, triangle order and the corner a triangle starts
                                       at, so adding the print uv layer (which re-splits vertices) does not change it
    charts_sha(charts, width, height)
                                    -> the packing's identity (each chart's part, kind, face count and atlas box)
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_JSON, _BIN = 0x4E4F534A, 0x004E4942
_DT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
M_TO_MM = 1000.0
TRANSFORM_KEYS = ("matrix", "translation", "rotation", "scale")
_SUFFIX = re.compile(r"\.\d{3}$")


@dataclass
class Prim:
    node: str                      # node name as written (the exporter restores authored object names)
    mesh: int
    index: int                     # primitive index inside the mesh
    material: int | None
    positions_mm: np.ndarray       # (N, 3) float64
    triangles: np.ndarray          # (M, 3) int64
    normals: np.ndarray | None     # (N, 3) float32 as stored
    texcoords: dict = field(default_factory=dict)      # n -> (N, 2) float32 as stored (glTF uv, v down)
    attributes: tuple = ()


def chunks(data: bytes) -> tuple[dict, bytes]:
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


def accessor(doc: dict, binc: bytes, index: int) -> np.ndarray:
    """(count, ncomp) array in the stored dtype (strided views handled; sparse and normalized accessors refused)."""
    acc = doc["accessors"][index]
    if "sparse" in acc or acc.get("normalized"):
        raise ValueError(f"accessor {index}: sparse / normalized accessors are not read here")
    dt = np.dtype(_DT[acc["componentType"]]).newbyteorder("<")
    nc = _NC[acc["type"]]
    count = int(acc["count"])
    view = doc["bufferViews"][acc["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise ValueError(f"accessor {index}: only the GLB's own buffer 0 is read here")
    off = int(view.get("byteOffset", 0)) + int(acc.get("byteOffset", 0))
    item = dt.itemsize * nc
    stride = int(view.get("byteStride") or item)
    if stride == item:
        return np.frombuffer(binc, dt, count * nc, off).reshape(count, nc).copy()
    raw = np.frombuffer(binc, np.uint8, stride * (count - 1) + item, off)
    rows = np.lib.stride_tricks.as_strided(raw, shape=(count, item), strides=(stride, 1))
    return np.frombuffer(rows.copy().tobytes(), dt).reshape(count, nc)


def read_glb(data: bytes | str | Path) -> list[Prim]:
    """Every TRIANGLES primitive of every mesh node, in node order. Node transforms are refused: the WP3 exporter
    bakes them (positions are asset metres), and so did the Studio export the host bakes on."""
    if not isinstance(data, (bytes, bytearray)):
        data = Path(data).read_bytes()
    doc, binc = chunks(bytes(data))
    out = []
    for node in doc.get("nodes", []):
        if "mesh" not in node:
            continue
        if any(k in node for k in TRANSFORM_KEYS):
            raise ValueError(f"node {node.get('name')!r} carries a transform; read only baked exports")
        mi = int(node["mesh"])
        for pi, prim in enumerate(doc["meshes"][mi]["primitives"]):
            if prim.get("mode", 4) != 4 or "indices" not in prim:
                continue
            attrs = prim.get("attributes", {})
            P = accessor(doc, binc, attrs["POSITION"]).astype(np.float64) * M_TO_MM
            T = accessor(doc, binc, prim["indices"]).reshape(-1, 3).astype(np.int64)
            N = accessor(doc, binc, attrs["NORMAL"]) if "NORMAL" in attrs else None
            tc = {int(k.split("_")[1]): accessor(doc, binc, v) for k, v in attrs.items() if k.startswith("TEXCOORD_")}
            out.append(Prim(node=node.get("name", ""), mesh=mi, index=pi, material=prim.get("material"),
                            positions_mm=P, triangles=T, normals=N, texcoords=tc, attributes=tuple(sorted(attrs))))
    return out


def mesh_arrays(prims: list[Prim], *, strip_suffix: bool = True
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """(V mm, F, per-face part id, part names) of all triangle primitives in node order, one part per primitive
    (the host's ``fitmesh.load_fit_mesh`` order; a multi-material object gives one part per material with the same
    name, and ``printuv.layout`` takes every part of that name). ``strip_suffix`` drops a Blender copy suffix
    ('.001', what the Studio's export wrote on q001.glb); g3d.print_uv reads the WP3 exporter's own GLB, whose node
    names are the object names exactly, and passes False."""
    Vs, Fs, part, names, off = [], [], [], [], 0
    for i, p in enumerate(prims):
        Vs.append(p.positions_mm)
        Fs.append(p.triangles + off)
        off += len(p.positions_mm)
        part.append(np.full(len(p.triangles), i, np.int64))
        names.append(_SUFFIX.sub("", p.node) if strip_suffix else p.node)   # 'Brow_Left.001' -> 'Brow_Left'
    if not Vs:
        raise ValueError("the GLB has no triangle primitives")
    return np.vstack(Vs), np.vstack(Fs), np.concatenate(part), names


def part_faces(part: np.ndarray, names: list[str], name: str) -> np.ndarray:
    ids = [i for i, n in enumerate(names) if n == name]
    return np.nonzero(np.isin(part, ids))[0]


def canonical_triangles(P: np.ndarray) -> np.ndarray:
    """(M, 3, 3) corner positions -> the same triangles, each rotated so its lexicographically smallest corner comes
    first (winding kept), sorted lexicographically. -0.0 is folded into +0.0."""
    P = np.asarray(P, np.float64).reshape(-1, 3, 3) + 0.0
    m = len(P)
    if not m:
        return P
    flat = P.reshape(-1, 3)
    order = np.lexsort((flat[:, 2], flat[:, 1], flat[:, 0]))
    rank = np.empty(len(flat), np.int64)
    rank[order] = np.arange(len(flat))
    r = np.argmin(rank.reshape(m, 3), axis=1)
    idx = (r[:, None] + np.arange(3)[None]) % 3
    Q = P[np.arange(m)[:, None], idx]
    keys = Q.reshape(m, 9)
    o = np.lexsort(tuple(keys[:, k] for k in range(8, -1, -1)))
    return np.ascontiguousarray(Q[o])


def geometry_sha(V: np.ndarray, F: np.ndarray, part: np.ndarray, names: list[str], parts) -> str:
    """The print geometry's identity (module docstring). Parts are hashed in the order given."""
    h = hashlib.sha256(b"g3d_print geometry v1|")
    V = np.asarray(V, np.float64)
    F = np.asarray(F, np.int64)
    for nm in parts:
        f = part_faces(part, names, nm)
        if not len(f):
            raise ValueError(f"part {nm!r} has no triangles")
        Q = canonical_triangles(V[F[f]])
        h.update(f"{nm}|{len(Q)}|".encode())
        h.update(Q.astype("<f8").tobytes())
    return h.hexdigest()


CHART_KEYS = ("part", "index_in_part", "kind", "faces", "x0", "y0", "w", "h")


def charts_sha(charts: list[dict], width: int, height: int) -> str:
    """The packing's identity: the atlas size and every chart's part, index, kind, face count and box."""
    rows = [[c[k] for k in CHART_KEYS] for c in charts]
    blob = json.dumps({"width": int(width), "height": int(height), "charts": rows}, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def uv_texels(prim: Prim, k: int, width: int, height: int) -> np.ndarray:
    """(M, 3, 2) TEXCOORD_k of each triangle corner in atlas texels (x = u * width, y = v * height; v down)."""
    uv = np.asarray(prim.texcoords[k], np.float64)
    return uv[prim.triangles] * np.array([width, height], np.float64)


__all__ = ["Prim", "read_glb", "mesh_arrays", "part_faces", "canonical_triangles", "geometry_sha", "charts_sha",
           "uv_texels", "chunks", "accessor"]
