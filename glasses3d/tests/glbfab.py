"""Deterministic synthetic GLBs for tests: a box-built pair of glasses that meets the owner's spec (PLAN.md §2), with
switches that break one requirement at a time. Units are metres; parts are boxes in millimetres converted at build."""
from __future__ import annotations

import base64
import json
import math
import struct
import zlib

import numpy as np

BOX_FACES = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                      [2, 3, 7], [2, 7, 6], [1, 2, 6], [1, 6, 5], [0, 4, 7], [0, 7, 3]], dtype=np.uint32)


def box(x0, x1, y0, y1, z0, z1):
    """8 corners (mm) of an axis-aligned box and its 12 outward triangles."""
    v = np.array([[x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
                  [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], dtype=np.float64)
    return v, BOX_FACES.copy()


def default_parts():
    """name -> (box mm, material key, partRole)."""
    return {
        "Bridge": ((-9, 9, 0, 4, -2, 2), "frame", "frame"),
        "Brow_L": ((-70, -9, 4, 10, -2, 2), "frame", "frame"),
        "Brow_R": ((9, 70, 4, 10, -2, 2), "frame", "frame"),
        "Lens_L": ((-60, -10, -25, 6, -1, 1), "lens", "lens"),
        "Lens_R": ((10, 60, -25, 6, -1, 1), "lens", "lens"),
        "Temple_L": ((-70, -66, 2, 8, -140, -2), "frame", "temple"),
        "Temple_R": ((66, 70, 2, 8, -140, -2), "frame", "temple"),
    }


def shield_parts():
    """A one-piece shield: lens-class halves and a lens nose notch (its underside is the bridge underside, at y=0),
    a frame top bar above it, and two temples."""
    return {
        "Shield_L": ((-65, -8, -30, 15, -1, 1), "lens", "lens"),
        "Notch": ((-8, 8, 0, 15, -1, 1), "lens", "lens"),
        "Shield_R": ((8, 65, -30, 15, -1, 1), "lens", "lens"),
        "Top_bar": ((-70, 70, 15, 20, -2, 2), "frame", "frame"),
        "Temple_L": ((-70, -66, 12, 18, -140, -2), "frame", "temple"),
        "Temple_R": ((66, 70, 12, 18, -140, -2), "frame", "temple"),
    }


def rot(axis: str, deg: float) -> np.ndarray:
    """4x4 rotation about a coordinate axis through the origin."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    m = np.eye(4)
    i, j = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
    m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
    return m


def trans(x, y, z) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = [x, y, z]
    return m


def png_bytes(w: int, h: int) -> bytes:
    raw = b"".join(b"\x00" + b"\x80" * (w * 3) for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def strip_indices(faces: np.ndarray) -> np.ndarray:
    """A TRIANGLE_STRIP that contains every triangle of `faces` (stitched with degenerate triangles)."""
    strip = list(faces[0])
    for t in faces[1:]:
        strip += [strip[-1], t[0], t[0], t[1], t[2]]
    return np.asarray(strip, dtype=np.uint32)


def build(parts=None, *, scale=0.001, roles=True, materials=None, node_matrix=None, parent_matrix=None,
          rotate_z_up=False, nan=False, mode=None, morph=False, animation=False, compressed=False,
          texture_px=None, data_uri_buffer=False, strided=False, sparse_shift=None, material_names=None,
          role_key="partRole", role_where="mesh", offset_mm=(0.0, 0.0, 0.0), pre_mm=None, target="Bridge",
          no_material=(), index_value=None, index_count=None, index_component=None, patch=None) -> bytes:
    """Return GLB bytes. Every switch breaks (or exercises) one thing; defaults meet the spec.

    `target` is the part that nan / mode / morph / compressed / index_value / index_count / index_component apply to
    (`compressed="all"` compresses every part). `pre_mm` is a 4x4 transform applied to every box (in mm) before
    `offset_mm`. `mode=5` writes the target as a valid TRIANGLE_STRIP. `index_component` stores the target's indices with
    that glTF componentType (5121/5123/5125 are valid; 5120/5122 are the signed types glTF forbids). `patch(doc, add_accessor)` edits the JSON last;
    add_accessor(array, componentType, type) stores data and returns the accessor index."""
    parts = default_parts() if parts is None else parts
    names = material_names or {"frame": "Frame acetate", "lens": "Lens clear"}
    mats = materials or {
        "frame": {"name": names["frame"], "pbrMetallicRoughness": {"baseColorFactor": [0.4, 0.2, 0.1, 1.0]}},
        "lens": {"name": names["lens"], "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1]},
                 "extensions": {"KHR_materials_transmission": {"transmissionFactor": 1.0}}},
    }
    mat_keys = list(mats)
    blob = bytearray()
    views, accessors, meshes, nodes = [], [], [], []

    def add_view(data: bytes, target_=None, stride=None):
        while len(blob) % 4:
            blob.append(0)
        v = {"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)}
        if target_:
            v["target"] = target_
        if stride:
            v["byteStride"] = stride
        blob.extend(data)
        views.append(v)
        return len(views) - 1

    def add_accessor(arr, component=5126, type_="VEC3"):
        dt = {5126: "<f4", 5125: "<u4", 5123: "<u2", 5121: "u1"}[component]
        a = np.asarray(arr).astype(dt)
        acc = {"bufferView": add_view(a.tobytes()), "componentType": component,
               "count": len(a), "type": type_}
        if component == 5126 and type_ == "VEC3" and len(a):
            acc["min"], acc["max"] = a.min(0).tolist(), a.max(0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    for name, (bx, mkey, role) in parts.items():
        v, f = box(*bx)
        if pre_mm is not None:
            v = (np.c_[v, np.ones(len(v))] @ np.asarray(pre_mm, float).T)[:, :3]
        v = (v + np.asarray(offset_mm)) * scale
        if rotate_z_up:                       # +Y up -> +Z up (a common exporter mistake)
            v = v[:, [0, 2, 1]] * np.array([1, -1, 1])
        hit = name == target
        if nan and hit:
            v[0, 0] = np.nan
        pos = v.astype("<f4")
        if strided:
            inter = np.zeros((len(pos), 6), "<f4")
            inter[:, :3] = pos
            pv = add_view(inter.tobytes(), 34962, stride=24)
        else:
            pv = add_view(pos.tobytes(), 34962)
        pacc = {"bufferView": pv, "componentType": 5126, "count": len(pos), "type": "VEC3",
                "min": np.nan_to_num(pos.min(0)).tolist(), "max": np.nan_to_num(pos.max(0)).tolist()}
        if sparse_shift is not None and hit:
            iv = add_view(np.array([0], "<u4").tobytes())
            vv = add_view(np.array(sparse_shift, "<f4").tobytes())
            pacc["sparse"] = {"count": 1, "indices": {"bufferView": iv, "componentType": 5125},
                              "values": {"bufferView": vv}}
        accessors.append(pacc)
        pa = len(accessors) - 1
        idx = (strip_indices(f) if (mode == 5 and hit) else f.reshape(-1)).astype(np.int64)
        if index_value is not None and hit:
            idx[0] = index_value
        ict = index_component if (index_component is not None and hit) else 5125
        idt = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4"}[ict]
        iv = add_view(idx.astype(idt).tobytes(), 34963)
        count = index_count if (index_count is not None and hit) else idx.size
        accessors.append({"bufferView": iv, "componentType": ict, "count": count, "type": "SCALAR"})
        ia = len(accessors) - 1
        prim = {"attributes": {"POSITION": pa}, "indices": ia}
        if name not in no_material:
            prim["material"] = mat_keys.index(mkey)
        if mode is not None and hit:
            prim["mode"] = mode
        if morph and hit:
            prim["targets"] = [{"POSITION": pa}]
        if compressed == "all" or (compressed and hit):
            prim["extensions"] = {"KHR_draco_mesh_compression": {"bufferView": iv, "attributes": {"POSITION": 0}}}
        mesh = {"name": name, "primitives": [prim]}
        node = {"name": name, "mesh": len(meshes)}
        if roles:
            tgt = mesh if role_where == "mesh" else node
            tgt["extras"] = {role_key: role}
        if node_matrix is not None and hit:
            node["matrix"] = list(np.asarray(node_matrix, float).T.reshape(-1))
        meshes.append(mesh)
        nodes.append(node)

    doc = {"asset": {"version": "2.0", "generator": "glasses3d tests"}, "scene": 0,
           "materials": [mats[k] for k in mat_keys], "meshes": meshes, "accessors": accessors,
           "bufferViews": views}
    root_children = list(range(len(nodes)))
    if parent_matrix is not None:
        nodes.append({"name": "Root", "children": root_children,
                      "matrix": list(np.asarray(parent_matrix, float).T.reshape(-1))})
        doc["scenes"] = [{"nodes": [len(nodes) - 1]}]
    else:
        doc["scenes"] = [{"nodes": root_children}]
    doc["nodes"] = nodes
    if texture_px:
        png = png_bytes(*texture_px)
        tv = add_view(png)
        doc["images"] = [{"bufferView": tv, "mimeType": "image/png"}]
        doc["textures"] = [{"source": 0}]
        doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
    if animation:
        doc["animations"] = [{"channels": [], "samplers": []}]
    if compressed:
        doc["extensionsUsed"] = ["KHR_draco_mesh_compression"]
    if any("extensions" in m for m in doc["materials"]):
        doc.setdefault("extensionsUsed", []).append("KHR_materials_transmission")
    if patch is not None:
        patch(doc, add_accessor)
    while len(blob) % 4:
        blob.append(0)
    if data_uri_buffer:
        doc["buffers"] = [{"byteLength": len(blob),
                           "uri": "data:application/octet-stream;base64," + base64.b64encode(bytes(blob)).decode()}]
        blob = bytearray()
    else:
        doc["buffers"] = [{"byteLength": len(blob)}]
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    out = struct.pack("<II", len(js), 0x4E4F534A) + js
    if blob:
        out += struct.pack("<II", len(blob), 0x004E4942) + bytes(blob)
    return b"glTF" + struct.pack("<II", 2, 12 + len(out)) + out
