"""Apply one smooth world-space deformation to a static GLB without rebaking it.

Only geometry in the selected scene is changed. Existing binary data, UVs,
indices, materials, textures, extras, and node transforms are retained. Each
node instance receives its own mesh and new geometry accessors, so shared source
meshes/accessors cannot make one instance overwrite another.

The callback returns (new_world_positions, world_jacobians), with Jacobian
J[i,a,b] = d(new_position[a])/d(old_position[b]). Units are the source GLB's
units. Normals use inverse-transpose transport; tangent directions use J.
This does not certify global injectivity or product fidelity: the fitter must
establish those. Skinning, animation, morphs, and compressed geometry are refused.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
from typing import Callable

import numpy as np

from .mesh import _node_matrix

Deformation = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]
_JSON = 0x4E4F534A
_BIN = 0x004E4942


def _read(path):
    return _read_bytes(Path(path).read_bytes())


def _read_bytes(raw):
    """Validate/decode exactly the captured bytes that carry the caller's pin."""
    if not isinstance(raw, bytes):
        raise ValueError('GLB input must be captured bytes')
    if len(raw) < 20 or struct.unpack_from("<4sII", raw) != (b"glTF", 2, len(raw)):
        raise ValueError("Expected an intact GLB version 2")
    chunks, offset = {}, 12
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("Truncated GLB chunk header")
        size, kind = struct.unpack_from("<II", raw, offset)
        offset += 8
        if size % 4 or offset + size > len(raw) or kind in chunks:
            raise ValueError("Invalid GLB chunk")
        chunks[kind] = raw[offset:offset + size]
        offset += size
    if _JSON not in chunks or set(chunks) - {_JSON, _BIN}:
        raise ValueError("Only standard JSON and BIN chunks are supported")
    doc = json.loads(chunks[_JSON])
    binary = chunks.get(_BIN, b"")
    buffers = doc.get("buffers", [])
    if len(buffers) != 1 or "uri" in buffers[0] or not 0 <= buffers[0]["byteLength"] <= len(binary):
        raise ValueError("Only a single embedded buffer is supported")
    if doc.get("skins") or doc.get("animations"):
        raise ValueError("Bake skinning and animation before deformation")
    if any("EXT_meshopt_compression" in v.get("extensions", {}) for v in doc.get("bufferViews", [])):
        raise ValueError("Decode compressed geometry before deformation")
    return raw, doc, binary


def _float_accessor(doc, binary, index, width):
    acc = doc["accessors"][index]
    if acc.get("componentType") != 5126 or acc.get("type") != f"VEC{width}" or acc.get("normalized") or "sparse" in acc or acc.get("extensions"):
        raise ValueError("Geometry requires ordinary float32 vector accessors")
    view = doc["bufferViews"][acc["bufferView"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride, count = view.get("byteStride", width * 4), acc["count"]
    end = start + max(0, count - 1) * stride + width * 4
    if view.get("buffer", 0) != 0 or count <= 0 or start < view.get("byteOffset", 0) or start < 0 or stride < width * 4 or stride % 4 or end > doc["buffers"][0]["byteLength"] or end > view.get("byteOffset", 0) + view["byteLength"]:
        raise ValueError("Invalid geometry accessor range")
    values = np.ndarray((count, width), dtype="<f4", buffer=binary, offset=start, strides=(stride, 4)).astype(np.float64)
    if not np.isfinite(values).all():
        raise ValueError("Geometry attributes must be finite")
    return values


def _unit(values):
    lengths = np.linalg.norm(values, axis=1)
    if not np.isfinite(values).all() or np.any(lengths <= 1e-12):
        raise ValueError("Cannot transport a zero or nonfinite direction")
    return values / lengths[:, None]


def _triangles(doc, binary, primitive, vertex_count):
    if "indices" not in primitive:
        indices = np.arange(vertex_count)
    else:
        acc = doc["accessors"][primitive["indices"]]
        if acc.get("type") != "SCALAR" or acc.get("componentType") not in (5121, 5123, 5125) or acc.get("normalized") or "sparse" in acc or acc.get("extensions"):
            raise ValueError("Triangle indices require an ordinary unsigned accessor")
        view = doc["bufferViews"][acc["bufferView"]]
        dtype = np.dtype({5121: "u1", 5123: "<u2", 5125: "<u4"}[acc["componentType"]])
        start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        count, stride = acc["count"], view.get("byteStride", dtype.itemsize)
        end = start + max(0, count - 1)*stride + dtype.itemsize
        if view.get("buffer", 0) != 0 or count <= 0 or start < view.get("byteOffset", 0) or start < 0 or stride < dtype.itemsize or end > doc["buffers"][0]["byteLength"] or end > view.get("byteOffset", 0) + view["byteLength"]:
            raise ValueError("Invalid index accessor range")
        indices = np.ndarray((count,), dtype=dtype, buffer=binary, offset=start, strides=(stride,)).astype(np.int64)
    if not len(indices) or len(indices) % 3 or indices.max() >= vertex_count:
        raise ValueError("Invalid triangle indices")
    return indices.reshape((-1, 3))


def _discrete_geometry(original, moved, jacobian, triangles, deformation):
    """Check retained triangles, independently of local differential validity.

    Positive J protects an infinitesimal surface only. Finite triangle chords
    can fold under a smooth field, so compare each chord normal to the average
    cofactor-transported source normal and explicitly reject collapse/folding.
    This still does not test intersections between separate triangles.
    """
    old, new = original[triangles], moved[triangles]
    old_cross = np.cross(old[:, 1]-old[:, 0], old[:, 2]-old[:, 0])
    new_cross = np.cross(new[:, 1]-new[:, 0], new[:, 2]-new[:, 0])
    old_area2, new_area2 = np.linalg.norm(old_cross, axis=1), np.linalg.norm(new_cross, axis=1)
    extent = float(np.max(np.ptp(original, axis=0)))
    area_floor = max(extent*extent*1e-14, np.finfo(float).tiny)
    valid = old_area2 > area_floor
    j = jacobian[triangles]
    transported = np.linalg.solve(j.transpose(0, 1, 3, 2), np.broadcast_to(old_cross[:, None, :, None], (len(triangles), 3, 3, 1)))[..., 0]
    transported = (transported*np.linalg.det(j)[..., None]).mean(axis=1)
    expected_length = np.linalg.norm(transported, axis=1)
    cosine = np.sum(new_cross*transported, axis=1) / np.maximum(new_area2*expected_length, np.finfo(float).tiny)
    collapsed = valid & (new_area2 <= old_area2*1e-6)
    inverted = valid & ~collapsed & (cosine <= 0)
    if np.any(collapsed) or np.any(inverted):
        raise ValueError(f"Deformation invalidates retained triangles: {int(collapsed.sum())} collapsed, {int(inverted.sum())} inverted")
    # Evaluate actual field at edge midpoints. Zero J residual at vertices alone
    # gives no upper bound on approximation error inside a retained triangle.
    source_mid = (old + old[:, [1, 2, 0]])*.5
    chord_mid = (new + new[:, [1, 2, 0]])*.5
    actual_mid, mid_j = deformation(source_mid.reshape((-1, 3)).copy())
    actual_mid, mid_j = np.asarray(actual_mid), np.asarray(mid_j)
    if actual_mid.shape != (3*len(triangles), 3) or mid_j.shape != (3*len(triangles), 3, 3) or not np.isfinite(actual_mid).all() or not np.isfinite(mid_j).all():
        raise ValueError("Invalid deformation at triangle edge midpoints")
    if np.any(np.linalg.det(mid_j) <= 1e-10):
        raise ValueError("Non-positive deformation Jacobian at triangle edge midpoints")
    discrepancy = np.linalg.norm(actual_mid.reshape((-1, 3, 3))-chord_mid, axis=2)
    return {"triangles_checked": len(triangles), "source_near_degenerate_triangles": int((~valid).sum()),
            "collapsed_triangles": 0, "inverted_triangles": 0,
            "minimum_area_ratio": float(np.min(new_area2[valid]/old_area2[valid])) if valid.any() else None,
            "minimum_transported_normal_cosine": float(cosine[valid].min()) if valid.any() else None,
            "midpoint_samples": int(discrepancy.size),
            "midpoints_above_relative_1e_9": int((discrepancy > max(extent*1e-9, np.finfo(float).tiny)).sum()),
            "max_midpoint_discrepancy_world_units": float(discrepancy.max())}


def deform_glb(source: str | Path, destination: str | Path, deformation: Deformation) -> dict:
    """Write a separate GLB using the callback's world-space positions and J.

    The source is never overwritten. All callbacks and validation finish before
    the destination is written. Singular/non-positive J, collapsed triangles,
    and triangles opposed to the transported surface normal are refused.
    """
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ValueError("Deformation must write a separate candidate")
    raw, doc, original_binary = _read(source)
    binary = bytearray(original_binary)
    nodes = doc.get("nodes", [])
    selected_scene = doc.get("scene", 0)
    transforms = {}

    def visit(index, parent, ancestors):
        if type(index) is not int or not 0 <= index < len(nodes) or index in ancestors:
            raise ValueError("Invalid or cyclic scene graph")
        matrix = parent @ _node_matrix(nodes[index])
        if index in transforms:
            raise ValueError("A selected-scene node cannot have multiple parents")
        if abs(np.linalg.det(matrix[:3, :3])) < 1e-12:
            raise ValueError("Singular node transform")
        if "EXT_mesh_gpu_instancing" in nodes[index].get("extensions", {}):
            raise ValueError("Expand GPU instances before deformation")
        transforms[index] = matrix
        for child in nodes[index].get("children", []):
            visit(child, matrix, ancestors | {index})

    for root in doc["scenes"][selected_scene].get("nodes", []):
        visit(root, np.eye(4), set())

    def append(values, source_accessor, position=False):
        values = np.asarray(values, dtype="<f4")
        if not np.isfinite(values).all():
            raise ValueError("Deformed geometry overflows float32")
        binary.extend(b"\0" * (-len(binary) % 4))
        view_index = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": values.nbytes, "target": 34962})
        binary.extend(values.tobytes())
        acc = deepcopy(doc["accessors"][source_accessor])
        acc.update(bufferView=view_index, byteOffset=0, componentType=5126, count=len(values), type=f"VEC{values.shape[1]}")
        if position or "min" in acc:
            acc["min"] = values.min(axis=0).tolist()
        if position or "max" in acc:
            acc["max"] = values.max(axis=0).tolist()
        doc["accessors"].append(acc)
        return len(doc["accessors"]) - 1

    changed_nodes, primitive_count, vertex_count, max_displacement = [], 0, 0, 0.0
    determinant_min, determinant_max = float("inf"), float("-inf")
    discrete_reports = []
    original_mesh_count = len(doc.get("meshes", []))
    for node_index, world in transforms.items():
        node = nodes[node_index]
        if "mesh" not in node:
            continue
        if "skin" in node or node.get("weights"):
            raise ValueError("Bake skinning and morphs before deformation")
        mesh = deepcopy(doc["meshes"][node["mesh"]])
        if mesh.get("weights"):
            raise ValueError("Bake morphs before deformation")
        linear, translation = world[:3, :3], world[:3, 3]
        inverse = np.linalg.inv(linear)
        for primitive in mesh["primitives"]:
            if primitive.get("mode", 4) != 4 or primitive.get("targets"):
                raise ValueError("Only static triangle primitives are supported")
            if "KHR_draco_mesh_compression" in primitive.get("extensions", {}):
                raise ValueError("Decode compressed geometry before deformation")
            attrs = primitive["attributes"]
            positions = _float_accessor(doc, original_binary, attrs["POSITION"], 3)
            world_positions = positions @ linear.T + translation
            moved, jacobian = deformation(world_positions.copy())
            moved, jacobian = np.asarray(moved, dtype=np.float64), np.asarray(jacobian, dtype=np.float64)
            if moved.shape != world_positions.shape or jacobian.shape != (len(positions), 3, 3) or not np.isfinite(moved).all() or not np.isfinite(jacobian).all():
                raise ValueError("Deformation must return finite (N,3) positions and (N,3,3) Jacobians")
            determinant = np.linalg.det(jacobian)
            if np.any(determinant <= 1e-10):
                raise ValueError("Deformation Jacobian must be nonsingular and orientation preserving")
            local_jacobian = inverse[None] @ jacobian @ linear[None]
            local_moved = (moved - translation) @ inverse.T
            # Check the actual float32 positions that the GLB will contain,
            # including quantization under the preserved node transform.
            quantized_world = local_moved.astype("<f4").astype(np.float64) @ linear.T + translation
            if not np.isfinite(quantized_world).all():
                raise ValueError("Deformed geometry overflows float32")
            triangles = _triangles(doc, original_binary, primitive, len(positions))
            discrete_reports.append(_discrete_geometry(world_positions, quantized_world, jacobian, triangles, deformation))
            normal = None
            # Read original normals/tangents before replacing any accessor.
            if "NORMAL" in attrs:
                normal = _float_accessor(doc, original_binary, attrs["NORMAL"], 3)
                if len(normal) != len(positions):
                    raise ValueError("Attribute counts must match POSITION")
                normal = _unit(np.linalg.solve(local_jacobian.transpose(0, 2, 1), normal[..., None])[..., 0])
                attrs["NORMAL"] = append(normal, attrs["NORMAL"])
            if "TANGENT" in attrs:
                tangent = _float_accessor(doc, original_binary, attrs["TANGENT"], 4)
                if len(tangent) != len(positions) or not np.all(np.isin(tangent[:, 3], [-1.0, 1.0])):
                    raise ValueError("Invalid tangent count or handedness")
                direction = np.einsum("nij,nj->ni", local_jacobian, tangent[:, :3])
                if normal is not None:
                    direction -= np.sum(direction * normal, axis=1)[:, None] * normal
                tangent[:, :3] = _unit(direction)
                attrs["TANGENT"] = append(tangent, attrs["TANGENT"])
            attrs["POSITION"] = append(local_moved, attrs["POSITION"], position=True)
            primitive_count += 1
            vertex_count += len(positions)
            max_displacement = max(max_displacement, float(np.linalg.norm(moved - world_positions, axis=1).max()))
            determinant_min = min(determinant_min, float(determinant.min()))
            determinant_max = max(determinant_max, float(determinant.max()))
        node["mesh"] = len(doc["meshes"])
        doc["meshes"].append(mesh)
        changed_nodes.append(node_index)
    if not changed_nodes:
        raise ValueError("Selected scene contains no deformable geometry")
    doc["buffers"][0]["byteLength"] = len(binary)
    payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    binary.extend(b"\0" * (-len(binary) % 4))
    encoded = (struct.pack("<4sII", b"glTF", 2, 28 + len(payload) + len(binary))
               + struct.pack("<II", len(payload), _JSON) + payload
               + struct.pack("<II", len(binary), _BIN) + binary)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(encoded)
    return {"source_sha256": hashlib.sha256(raw).hexdigest(), "output_sha256": hashlib.sha256(encoded).hexdigest(),
            "selected_scene": selected_scene, "changed_node_indices": changed_nodes,
            "new_mesh_instances": len(doc["meshes"]) - original_mesh_count,
            "primitives": primitive_count, "vertices": vertex_count,
            "max_displacement_world_units": max_displacement,
            "jacobian_determinant_min": determinant_min, "jacobian_determinant_max": determinant_max,
            "discrete_geometry": {
                "triangles_checked": sum(r["triangles_checked"] for r in discrete_reports),
                "source_near_degenerate_triangles": sum(r["source_near_degenerate_triangles"] for r in discrete_reports),
                "collapsed_triangles": 0, "inverted_triangles": 0,
                "minimum_area_ratio": min((r["minimum_area_ratio"] for r in discrete_reports if r["minimum_area_ratio"] is not None), default=None),
                "minimum_transported_normal_cosine": min((r["minimum_transported_normal_cosine"] for r in discrete_reports if r["minimum_transported_normal_cosine"] is not None), default=None),
                "midpoint_samples": sum(r["midpoint_samples"] for r in discrete_reports),
                "midpoints_above_relative_1e_9": sum(r["midpoints_above_relative_1e_9"] for r in discrete_reports),
                "max_midpoint_discrepancy_world_units": max(r["max_midpoint_discrepancy_world_units"] for r in discrete_reports),
                "global_self_intersections_checked": False},
            "original_binary_prefix_preserved": bytes(binary[:len(original_binary)]) == original_binary,
            "topology_uv_materials_preserved": True,
            "limitations": ["The caller must certify the deformation field and geometry quality.",
                            "Vertex-sampled Jacobians do not prove injectivity between vertices.",
                            "The existing triangulation approximates a nonlinear field between vertices.",
                            "Edge-midpoint discrepancies are samples, not a certified global error bound.",
                            "Intersections between separate triangles are not checked."]}
