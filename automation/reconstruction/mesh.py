"""Read static, embedded GLB triangle geometry without executing model code.

Coordinates remain glTF world coordinates: Y up, with the AR assets facing +Z.
Materials are recorded, not simulated. Silhouette rendering cannot validate tint.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import struct

import numpy as np


@dataclass
class TriangleMesh:
    vertices: np.ndarray
    faces: np.ndarray
    parts: list[dict]

    def normalized(self):
        lo, hi = self.vertices.min(axis=0), self.vertices.max(axis=0)
        extent = float(np.max(hi - lo))
        if extent <= 0:
            raise ValueError("Mesh has no spatial extent")
        return TriangleMesh((self.vertices - (lo + hi) / 2) / extent, self.faces, self.parts)


def _node_matrix(node):
    if "matrix" in node:
        matrix = np.asarray(node["matrix"], dtype=float).reshape((4, 4), order="F")
    else:
        x, y, z, w = np.asarray(node.get("rotation", [0, 0, 0, 1]), dtype=float)
        if not np.isclose(x*x + y*y + z*z + w*w, 1, atol=1e-5):
            raise ValueError("Node quaternion must have unit length")
        rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                             [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                             [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
        matrix = np.eye(4)
        matrix[:3, :3] = rotation @ np.diag(node.get("scale", [1, 1, 1]))
        matrix[:3, 3] = node.get("translation", [0, 0, 0])
    if not np.isfinite(matrix).all() or not np.allclose(matrix[3], [0, 0, 0, 1]):
        raise ValueError("Invalid affine node transform")
    return matrix


def load_glb(path: str | Path) -> TriangleMesh:
    return load_glb_bytes(Path(path).read_bytes())


def load_glb_bytes(raw: bytes) -> TriangleMesh:
    """Decode one captured GLB byte string, so geometry shares its caller's pin."""
    if not isinstance(raw, bytes):
        raise ValueError("GLB input must be captured bytes")
    if len(raw) < 20:
        raise ValueError("Truncated GLB")
    magic, version, length = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or length != len(raw):
        raise ValueError("Expected an intact GLB version 2")
    chunks, offset = {}, 12
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("Truncated GLB chunk header")
        size, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        if offset + size > len(raw) or kind in chunks:
            raise ValueError("Truncated or duplicate GLB chunk")
        chunks[kind] = raw[offset:offset + size]
        offset += size
    document = json.loads(chunks[0x4E4F534A])
    binary = chunks.get(0x004E4942, b"")
    if document.get("skins") or document.get("animations"):
        raise ValueError("Evaluator requires a static model; bake animation/skinning first")
    buffers = document.get("buffers", [])
    if len(buffers) != 1 or "uri" in buffers[0] or buffers[0]["byteLength"] > len(binary):
        raise ValueError("Only a single embedded buffer is supported")
    dtypes = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
    widths = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

    def accessor(index):
        acc = document["accessors"][index]
        if "sparse" in acc or acc.get("normalized"):
            raise ValueError("Sparse/normalized geometry accessors require preprocessing")
        view = document["bufferViews"][acc["bufferView"]]
        if view.get("buffer", 0) != 0:
            raise ValueError("External buffers are not supported")
        dtype, width, count = np.dtype(dtypes[acc["componentType"]]), widths[acc["type"]], acc["count"]
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride", width * dtype.itemsize)
        end = start + max(0, count - 1) * stride + (width * dtype.itemsize if count else 0)
        if count <= 0 or start < view.get("byteOffset", 0) or start < 0 or stride < width * dtype.itemsize or end > buffers[0]["byteLength"] or end > view.get("byteOffset", 0) + view["byteLength"]:
            raise ValueError("Invalid geometry accessor range")
        return np.ndarray((count, width), dtype=dtype, buffer=binary, offset=start,
                          strides=(stride, dtype.itemsize)).copy()

    vertices, faces, parts, vertex_count, face_count = [], [], [], 0, 0
    nodes = document.get("nodes", [])

    def visit(index, parent, ancestors):
        nonlocal vertex_count, face_count
        if type(index) is not int or not 0 <= index < len(nodes) or index in ancestors:
            raise ValueError("Invalid or cyclic scene graph")
        node = nodes[index]
        transform = parent @ _node_matrix(node)
        if "mesh" in node:
            mesh = document["meshes"][node["mesh"]]
            for primitive_index, primitive in enumerate(mesh["primitives"]):
                if primitive.get("mode", 4) != 4 or primitive.get("targets"):
                    raise ValueError("Only static triangle primitives are supported")
                if "KHR_draco_mesh_compression" in primitive.get("extensions", {}):
                    raise ValueError("Decode Draco geometry before evaluation")
                material = document.get("materials", [])[primitive["material"]] if "material" in primitive else {}
                alpha = material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1])[3]
                if material.get("alphaMode") == "BLEND" and alpha == 0 and "LENSES_lens_appearance" not in material.get("extensions", {}):
                    continue
                points = accessor(primitive["attributes"]["POSITION"])
                if points.shape[1] != 3 or not np.isfinite(points).all():
                    raise ValueError("Geometry positions must be finite 3-vectors")
                indices = accessor(primitive["indices"]).ravel() if "indices" in primitive else np.arange(len(points))
                if indices.dtype.kind not in "ui" or len(indices) % 3 or indices.min() < 0 or indices.max() >= len(points):
                    raise ValueError("Invalid triangle indices")
                points = points @ transform[:3, :3].T + transform[:3, 3]
                triangles = indices.reshape((-1, 3)).astype(np.int64) + vertex_count
                vertices.append(points)
                faces.append(triangles)
                parts.append({"name": node.get("name", mesh.get("name", "mesh")),
                              "node_index": index, "mesh_index": node["mesh"], "primitive_index": primitive_index,
                              "material_index": primitive.get("material"),
                              "material": material.get("name", "material"),
                              "declared_role": (primitive.get("extras") or {}).get("partRole") or node.get("extras", {}).get("partRole"),
                              "has_lens_appearance_extension": "LENSES_lens_appearance" in material.get("extensions", {}),
                              "face_start": face_count, "face_count": len(triangles),
                              "vertex_start": vertex_count, "vertex_count": len(points),
                              "transmission": material.get("extensions", {}).get("KHR_materials_transmission", {}).get("transmissionFactor", 0)})
                vertex_count += len(points)
                face_count += len(triangles)
        for child in node.get("children", []):
            visit(child, transform, ancestors | {index})

    scene = document["scenes"][document.get("scene", 0)]
    for index in scene.get("nodes", []):
        visit(index, np.eye(4), set())
    if not vertices:
        raise ValueError("No visible triangle geometry in the selected scene")
    return TriangleMesh(np.concatenate(vertices), np.concatenate(faces), parts)
