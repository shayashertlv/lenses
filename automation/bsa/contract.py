"""S9 contract check: does a GLB satisfy the BSA / AR external-asset contract (DESIGN.md S9)?

``check(path) -> dict`` parses the GLB (no three.js) and verifies:
units (front width 0.10-0.20 m), +Z front (lens centroid ahead of the temple mass), bridge-underside
origin, identity node transforms, node naming, the runtime lens-detection rule (lens materials
have transmission > 0 or a canonical lens descriptor; frame/temples opaque, or translucent only when
single-sided, descriptors are present and the mesh carries its own role), triangles <= 100k,
bytes <= 8 MB, textures <= 2048 px, finite attributes and unit normals, per-part watertightness
(frame and temples closed 2-manifolds; a lens is either a closed solid or a +Z front sheet - the
export rule of ``bsa.export``).

Every check is ``{"pass": bool, "value": ..., "limit": ...}``; ``ok`` is the AND of all checks.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image

from .export import FRAME_NODE, NODE_ORDER, PAIR_LENS_NODES, PART_ROLE, SINGLE_LENS_NODES, TEMPLE_NODES

MAX_BYTES = 8 * 1024 * 1024
MAX_TRIANGLES = 100_000
WIDTH_RANGE_M = (0.10, 0.20)
MAX_TEXTURE_PX = 2048
ORIGIN_TOL_XY_MM = 1.0
ORIGIN_TOL_Z_MM = 3.0
WELD_M = 1e-6          # 1 micron position weld for topology checks
CANONICAL_LENS_EXTENSION = "LENSES_lens_appearance"

_DT = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_W = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path_or_bytes) -> dict:
    """Parse a self-contained GLB: {doc, bytes, nodes: [{name, index, transform_identity, extras,
    primitives: [{P, N, UV, COLOR, I, material, material_index}]}], images: [{mime, size, bytes}]}."""
    raw = Path(path_or_bytes).read_bytes() if not isinstance(path_or_bytes, (bytes, bytearray)) else bytes(path_or_bytes)
    if len(raw) < 20:
        raise ValueError("Truncated GLB")
    magic, version, length = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or length != len(raw):
        raise ValueError("Expected an intact GLB version 2")
    off, chunks = 12, []
    while off < len(raw):
        size, kind = struct.unpack_from("<II", raw, off)
        off += 8
        if off + size > len(raw):
            raise ValueError("Truncated GLB chunk")
        chunks.append((kind, raw[off:off + size]))
        off += size
    if not chunks or chunks[0][0] != 0x4E4F534A:
        raise ValueError("First GLB chunk must be JSON")
    doc = json.loads(chunks[0][1])
    binary = next((c for k, c in chunks[1:] if k == 0x004E4942), b"")
    for res in doc.get("buffers", []) + doc.get("images", []):
        if "uri" in res and not str(res["uri"]).startswith("data:"):
            raise ValueError("External resources are not allowed")

    def accessor(i):
        a = doc["accessors"][i]
        bv = doc["bufferViews"][a["bufferView"]]
        dt, w = np.dtype(_DT[a["componentType"]]), _W[a["type"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", w * dt.itemsize)
        out = np.ndarray((a["count"], w), dtype=dt, buffer=binary, offset=start, strides=(stride, dt.itemsize)).copy()
        if a.get("normalized"):
            out = out.astype(float) / float(np.iinfo(dt).max)
        return out

    nodes = []
    for ni, node in enumerate(doc.get("nodes", [])):
        identity = True
        if "matrix" in node:
            identity = np.allclose(np.asarray(node["matrix"], float), np.eye(4).ravel(order="F"), atol=1e-9)
        identity &= np.allclose(node.get("translation", [0, 0, 0]), 0, atol=1e-12)
        identity &= np.allclose(node.get("rotation", [0, 0, 0, 1]), [0, 0, 0, 1], atol=1e-12)
        identity &= np.allclose(node.get("scale", [1, 1, 1]), 1, atol=1e-12)
        prims = []
        if "mesh" in node:
            for pr in doc["meshes"][node["mesh"]]["primitives"]:
                at = pr["attributes"]
                P = accessor(at["POSITION"]).astype(np.float64)
                I = accessor(pr["indices"]).ravel().astype(np.int64) if "indices" in pr else np.arange(len(P))
                mi = pr.get("material")
                prims.append({"P": P, "I": I, "mode": pr.get("mode", 4),
                              "N": accessor(at["NORMAL"]).astype(np.float64) if "NORMAL" in at else None,
                              "UV": accessor(at["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in at else None,
                              "COLOR": accessor(at["COLOR_0"]).astype(np.float64) if "COLOR_0" in at else None,
                              "material_index": mi, "material": doc.get("materials", [])[mi] if mi is not None else {}})
        nodes.append({"name": node.get("name", f"node_{ni}"), "index": ni, "transform_identity": bool(identity),
                      "children": node.get("children", []), "extras": node.get("extras", {}) or {},
                      "mesh_extras": (doc["meshes"][node["mesh"]].get("extras", {}) or {}) if "mesh" in node else {}, "primitives": prims})
    images = []
    for im in doc.get("images", []):
        if "bufferView" in im:
            bv = doc["bufferViews"][im["bufferView"]]
            data = binary[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
            try:
                with Image.open(io.BytesIO(data)) as pil:
                    size, fmt = list(pil.size), pil.format
            except Exception:  # noqa: BLE001
                size, fmt = None, None
            images.append({"name": im.get("name"), "mime": im.get("mimeType"), "format": fmt, "size": size, "bytes": data})
    return {"doc": doc, "bytes": raw, "nodes": nodes, "images": images}


def transmission_of(material: dict) -> float:
    return float(material.get("extensions", {}).get("KHR_materials_transmission", {}).get("transmissionFactor", 0.0))


def is_lens_material(material: dict) -> bool:
    """The runtime rule (ar/src/eyewear/optical-material.ts): transmission > 0 or a canonical descriptor."""
    return transmission_of(material) > 0 or CANONICAL_LENS_EXTENSION in material.get("extensions", {})


def _triangles(prims) -> tuple[np.ndarray, np.ndarray]:
    Ps, Fs, off = [], [], 0
    for p in prims:
        if p["mode"] != 4:
            continue
        Ps.append(p["P"])
        Fs.append(p["I"].reshape(-1, 3) + off)
        off += len(p["P"])
    if not Ps:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    return np.vstack(Ps), np.vstack(Fs)


def topology(P: np.ndarray, F: np.ndarray, weld: float = WELD_M) -> dict:
    """Weld positions and classify edges: closed (every edge in exactly 2 faces, opposite directions)."""
    if not len(F):
        return {"watertight": False, "faces": 0}
    key = np.round(P / weld).astype(np.int64)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    W = inv.ravel()[F]
    degenerate = (W[:, 0] == W[:, 1]) | (W[:, 1] == W[:, 2]) | (W[:, 2] == W[:, 0])
    W = W[~degenerate]
    d = np.concatenate([W[:, [0, 1]], W[:, [1, 2]], W[:, [2, 0]]])
    und = np.sort(d, axis=1)
    _, ucount = np.unique(und, axis=0, return_counts=True)
    _, dcount = np.unique(d, axis=0, return_counts=True)
    boundary = int((ucount == 1).sum())
    nonmanifold = int((ucount > 2).sum())
    misoriented = int((dcount > 1).sum())
    return {"watertight": boundary == 0 and nonmanifold == 0 and misoriented == 0, "faces": int(len(F)),
            "welded_vertices": int(inv.max() + 1), "boundary_edges": boundary, "nonmanifold_edges": nonmanifold,
            "misoriented_edges": misoriented, "degenerate_faces": int(degenerate.sum())}


def _face_normals(P, F):
    n = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    a2 = np.linalg.norm(n, axis=1)
    return n / np.maximum(a2, 1e-30)[:, None], a2 / 2


def check(path: str | Path, *, width_range_m=WIDTH_RANGE_M) -> dict:
    """Verify the contract. Returns {ok, checks, failures, parts, summary}."""
    checks: dict[str, dict] = {}

    def put(name, ok, value=None, limit=None, **more):
        checks[name] = {"pass": bool(ok), "value": value, "limit": limit, **more}

    path = Path(path)
    try:
        g = read_glb(path)
    except Exception as e:  # noqa: BLE001
        put("parse", False, str(e))
        return {"ok": False, "path": str(path), "checks": checks, "failures": ["parse"], "parts": {}}
    put("parse", True)
    doc = g["doc"]
    put("bytes", len(g["bytes"]) <= MAX_BYTES, len(g["bytes"]), MAX_BYTES)
    names = [n["name"] for n in g["nodes"] if n["primitives"]]
    lens_names = [n for n in names if PART_ROLE.get(n) == "lens"]
    naming_ok = (FRAME_NODE in names and set(names) <= set(NODE_ORDER) and len(set(names)) == len(names)
                 and sorted(lens_names) in (sorted(PAIR_LENS_NODES), list(SINGLE_LENS_NODES)))
    put("node_names", naming_ok, names, list(NODE_ORDER))
    put("temples_present", all(t in names for t in TEMPLE_NODES), [t for t in TEMPLE_NODES if t in names], list(TEMPLE_NODES))
    put("identity_transforms", all(n["transform_identity"] and not n["children"] for n in g["nodes"]),
        [n["name"] for n in g["nodes"] if not n["transform_identity"] or n["children"]], "none")
    parts, finite_ok, normals_ok, tri = {}, True, True, 0
    for n in g["nodes"]:
        if not n["primitives"]:
            continue
        P, F = _triangles(n["primitives"])
        tri += len(F)
        for p in n["primitives"]:
            for k in ("P", "N", "UV", "COLOR"):
                if p[k] is not None and not np.isfinite(p[k]).all():
                    finite_ok = False
            if p["N"] is not None and len(p["N"]) and np.abs(np.linalg.norm(p["N"], axis=1) - 1).max() > 1e-3:
                normals_ok = False
        mats = [p["material"] for p in n["primitives"]]
        parts[n["name"]] = {"P": P, "F": F, "mats": mats, "prims": n["primitives"], "extras": n["extras"], "mesh_extras": n["mesh_extras"]}
    put("finite", finite_ok)
    put("unit_normals", normals_ok)
    put("triangles", tri <= MAX_TRIANGLES, tri, MAX_TRIANGLES)
    # textures
    big = [im["size"] for im in g["images"] if im["size"] is None or max(im["size"]) > MAX_TEXTURE_PX]
    fmts = sorted({str(im["format"]) for im in g["images"]})
    put("textures", not big and set(fmts) <= {"JPEG", "PNG"}, {"count": len(g["images"]), "formats": fmts,
                                                            "max_px": max([max(im["size"]) for im in g["images"] if im["size"]] or [0])},
        {"max_px": MAX_TEXTURE_PX, "formats": ["JPEG", "PNG"]})
    # roles: lens nodes by name when the naming holds, else by material (as the runtime does)
    def role(name, info):
        if name in PART_ROLE:
            return PART_ROLE[name]
        return "lens" if any(is_lens_material(m) for m in info["mats"]) else "other"
    has_descriptors = any(CANONICAL_LENS_EXTENSION in m.get("extensions", {}) for v in parts.values() for m in v["mats"])

    def runtime_role(info):
        """The role the runtime reads (ar/src/eyewear/optical-material.ts partRoleOf): the node's partRole extra, else the
        mesh's; None when absent or unknown (then a transmissive material stays optical there)."""
        r = (info.get("extras") or {}).get("partRole") or (info.get("mesh_extras") or {}).get("partRole")
        return r if r in ("frame", "temple", "lens") else None

    def runtime_lens(m, rr):
        """The runtime rule with authored roles: a descriptor is optical; transmission > 0 is optical unless the asset
        carries descriptors and the mesh's authored role is frame/temple (then the material is frame)."""
        if CANONICAL_LENS_EXTENSION in m.get("extensions", {}):
            return True
        return transmission_of(m) > 0 and not (has_descriptors and rr in ("frame", "temple"))
    lens_parts = {k: v for k, v in parts.items() if role(k, v) == "lens"}
    temple_parts = {k: v for k, v in parts.items() if role(k, v) == "temple"}
    front_parts = {k: v for k, v in parts.items() if role(k, v) in ("frame", "lens")}
    detect = {k: all(runtime_lens(m, runtime_role(v)) for m in v["mats"]) for k, v in lens_parts.items()}
    materials_ok = {}
    for k, v in parts.items():
        r = role(k, v)
        if r == "lens":
            continue
        ok = all(m.get("alphaMode", "OPAQUE") == "OPAQUE" and CANONICAL_LENS_EXTENSION not in m.get("extensions", {}) for m in v["mats"])
        # one rule for the frame and the temples: a translucent part is allowed when the runtime will classify it by its
        # own role (descriptors present, the mesh carries the role its name promises) and it is single-sided
        ok &= all(transmission_of(m) <= 0 or (has_descriptors and runtime_role(v) == r and not m.get("doubleSided", False))
                  for m in v["mats"])
        materials_ok[k] = bool(ok)
    runtime_lens_meshes = sum(any(runtime_lens(m, runtime_role(v)) for m in v["mats"]) for k, v in parts.items())
    put("lens_detection", bool(lens_parts) and all(detect.values()), {"lens_parts": detect, "runtime_lens_meshes": runtime_lens_meshes},
        "every lens material transmission > 0 (or canonical descriptor)")
    put("frame_temple_materials", all(materials_ok.values()), materials_ok,
        "frame and temples opaque or translucent (transmission, single-sided, descriptors present, the part's own role); no lens descriptor off the lenses")
    # the roles the runtime reads must be the roles the names promise (the exporter writes both); a canonical lens mesh
    # also carries the mesh extras the canonical adapter validates (lens-material.ts validateCanonicalLensSurface)
    roles_report = {}
    for k, v in parts.items():
        if k not in PART_ROLE:
            continue
        expected = PART_ROLE[k]
        got = runtime_role(v)
        entry = {"expected": expected, "runtime_role": got, "pass": got == expected}
        if expected == "lens" and any(CANONICAL_LENS_EXTENSION in m.get("extensions", {}) for m in v["mats"]):
            me = v.get("mesh_extras") or {}
            entry["mesh_extras_pass"] = me.get("partRole") == "lens" and me.get("lensSurfaceProfile") == "front_sheet_v1"
            entry["pass"] = entry["pass"] and entry["mesh_extras_pass"]
        roles_report[k] = entry
    put("part_roles", all(e["pass"] for e in roles_report.values()) if naming_ok else True,
        roles_report if naming_ok else "n/a (foreign naming)", "node extras partRole == the named role; canonical lens meshes carry partRole/lensSurfaceProfile")
    # a material index used by a lens node and a non-lens node would keep the frame part optical in the runtime
    lens_mi = {p["material_index"] for v in lens_parts.values() for p in v["prims"] if p["material_index"] is not None}
    other_mi = {p["material_index"] for k, v in parts.items() if k not in lens_parts for p in v["prims"] if p["material_index"] is not None}
    put("lens_materials_private", not (lens_mi & other_mi), sorted(lens_mi & other_mi), "no material shared between lens and non-lens nodes")
    # units / orientation
    allP = np.vstack([v["P"] for v in parts.values()]) if parts else np.zeros((0, 3))
    fP = np.vstack([v["P"] for v in front_parts.values()]) if front_parts else allP
    width = float(np.ptp(fP[:, 0])) if len(fP) else 0.0
    put("units_width_m", width_range_m[0] <= width <= width_range_m[1], round(width, 5), list(width_range_m))
    temple_P = np.vstack([v["P"] for v in temple_parts.values()]) if temple_parts else np.zeros((0, 3))
    if lens_parts and not temple_parts:   # foreign naming: the temple mass = opaque material 20 mm behind the lenses
        lzmin = float(np.vstack([v["P"] for v in lens_parts.values()])[:, 2].min())
        other = [v["P"] for k, v in parts.items() if k not in lens_parts]
        temple_P = np.vstack(other) if other else temple_P
        temple_P = temple_P[temple_P[:, 2] < lzmin - 0.02]
    if lens_parts and len(temple_P):
        lz = float(np.vstack([v["P"] for v in lens_parts.values()])[:, 2].mean())
        tz = float(temple_P[:, 2].mean())
        put("front_is_plus_z", lz > tz + 0.01, {"lens_centroid_z_m": round(lz, 5), "temple_centroid_z_m": round(tz, 5)}, "lens z > temple z + 0.01")
    else:
        put("front_is_plus_z", False, "needs lens and temple parts")
    # origin: re-derive the bridge underside from the geometry (same rule as bsa.export)
    try:
        from .export import bridge_underside_mm
        src = front_parts if naming_ok else parts      # foreign GLB: every part, cut to the front slab below
        Vs, Fs, off = [], [], 0
        for v in src.values():
            Vs.append(v["P"] * 1000.0)
            Fs.append(v["F"] + off)
            off += len(v["P"])
        Vmm, Fmm = np.vstack(Vs), np.vstack(Fs)
        if not naming_ok:
            Fmm = Fmm[(Vmm[Fmm][:, :, 2] >= Vmm[:, 2].max() - 30.0).all(1)]
        o, _ = bridge_underside_mm({"frame": {"V": Vmm, "F": Fmm}}, axis_x=0.0)
        centre_x = float((fP[:, 0].min() + fP[:, 0].max()) / 2 * 1000)
        ok = abs(centre_x) <= ORIGIN_TOL_XY_MM and abs(o[1]) <= ORIGIN_TOL_XY_MM and abs(o[2]) <= ORIGIN_TOL_Z_MM
        put("origin_bridge_underside", ok, {"front_centre_x_mm": round(centre_x, 3), "bridge_underside_mm": np.round(o, 3).tolist()},
            {"xy_mm": ORIGIN_TOL_XY_MM, "z_mm": ORIGIN_TOL_Z_MM})
    except Exception as e:  # noqa: BLE001
        put("origin_bridge_underside", False, f"error: {e}")
    # per-part topology
    part_report, water_ok = {}, True
    for k, v in parts.items():
        if not np.isfinite(v["P"]).all():
            part_report[k] = {"role": role(k, v), "triangles": int(len(v["F"])), "watertight": False,
                              "boundary_edges": None, "nonmanifold_edges": None, "error": "non-finite positions", "pass": False}
            water_ok = False
            continue
        topo = topology(v["P"], v["F"])
        r = role(k, v)
        rep = {"role": r, "triangles": int(len(v["F"])), **topo}
        if r == "lens":
            fn, area = _face_normals(v["P"], v["F"])
            vn_ok = all(p["N"] is None or (p["N"][:, 2] > 0).all() for p in v["prims"])
            sheet = bool((fn[area > 0, 2] > 0).all()) and vn_ok
            rep["profile"] = "solid" if topo["watertight"] else ("front_sheet" if sheet else "open")
            rep["min_face_nz"] = float(fn[area > 0, 2].min()) if (area > 0).any() else None
            rep["double_sided"] = any(m.get("doubleSided", False) for m in v["mats"])
            ok = rep["profile"] in ("solid", "front_sheet")
        else:
            ok = topo["watertight"]
        rep["pass"] = bool(ok)
        water_ok &= ok
        part_report[k] = rep
    put("watertight_parts", water_ok, {k: (r.get("profile") or r["watertight"]) for k, r in part_report.items()},
        "frame/temples closed; lens closed or +Z front sheet")
    failures = [k for k, c in checks.items() if not c["pass"]]
    lo, hi = (allP.min(0), allP.max(0)) if len(allP) else (np.zeros(3), np.zeros(3))
    return {"ok": not failures, "path": str(path), "checks": checks, "failures": failures, "parts": part_report,
            "summary": {"bytes": len(g["bytes"]), "triangles": tri, "width_m": round(width, 5),
                        "bbox_m": [lo.round(5).tolist(), hi.round(5).tolist()], "nodes": names,
                        "runtime_lens_meshes": runtime_lens_meshes, "images": len(g["images"])}}
