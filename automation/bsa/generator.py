"""S1 `s1_generator`: the canonical generator in the MODEL frame (mm, +X right, +Y up, +Z front).

Input: the product's cached raw Tripo GLB (one textured primitive, ~1.9M triangles).

1. Read positions / indices / TEXCOORD_0 and the embedded texture bytes (node transforms applied).
2. Canonicalize: (x, y, z) -> (-z, y, x) (the yaw -90 prior), then VERIFY with three votes that the
   lens plate is at +Z (centre-column depth, end-slab lateral coverage, end-slab area); flip
   (x, z) -> (-x, -z) when the majority says the plate is at -Z. Also checks (reported, not
   corrected): X is the width axis (the +Z end carries the most area of the four horizontal
   ends) and +Y is up (temples sit above the front piece's mid-height).
3. Scale uniformly so the FRONT PIECE (geometry within 20 mm of the front-most depth, a fixed
   point in mm) spans ``Product.front_width_mm`` in X. No translation: the MODEL origin is the
   raw origin (Tripo's bbox centre); the NormFrame carries the centring.
4. NormFrame = bbox centre + max extent of the stored (float32) canonical vertices.
5. Decimate (merge duplicate positions, open3d quadric) to ~24k faces for camera fitting.
6. Save arrays, native texture files, a proof sheet and result.json.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import io
import json
import math
from pathlib import Path
import struct
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from reconstruction.camera import Camera

from . import raster
from .core import PRODUCTS, NormFrame, Product, load_photo, project_mm, sha256_file, stage_dir

STAGE = "s1_generator"
FRONT_SLAB_MM = 20.0          # contract: the front piece is within 20 mm of the front-most depth ...
FRONT_KNEE_DEG = 10.0         # ... extended while the lateral outline still wraps outward (> 10 deg per side)
FRONT_KNEE_WINDOW_MM = 10.0   #     measured over the next 10 mm of depth
FRONT_KNEE_STEP_MM = 0.25
DECIMATED_FACES = 24_000
R_YAW_M90 = np.array([[0.0, 0.0, -1.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]])   # (x,y,z) -> (-z,y,x)
R_FLIP = np.diag([-1.0, 1.0, -1.0])                                           # (x,y,z) -> (-x,y,-z)
SHEET_PX_PER_MM = 4.0

_DTYPES = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}
_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


# --------------------------------------------------------------------------- GLB reading
@dataclass
class RawGLB:
    positions: np.ndarray            # (N,3) float64, node transforms applied
    faces: np.ndarray                # (M,3) int64
    uv: np.ndarray | None            # (N,2) float32 glTF UV (v down) or None
    textures: dict[str, tuple[str, bytes]] = field(default_factory=dict)   # slot -> (mime, bytes)
    primitives: int = 1
    materials: list[str] = field(default_factory=list)


def _node_matrix(node: dict) -> np.ndarray:
    if "matrix" in node:
        return np.asarray(node["matrix"], float).reshape((4, 4), order="F")
    x, y, z, w = np.asarray(node.get("rotation", [0, 0, 0, 1]), float)
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4)
    M[:3, :3] = R @ np.diag(node.get("scale", [1, 1, 1]))
    M[:3, 3] = node.get("translation", [0, 0, 0])
    return M


def read_glb(path: Path) -> RawGLB:
    """Geometry, UVs and embedded texture bytes of a static triangle GLB (single embedded buffer)."""
    raw = Path(path).read_bytes()
    magic, version, length = struct.unpack_from("<4sII", raw)
    if magic != b"glTF" or version != 2 or length != len(raw):
        raise ValueError(f"{path}: not an intact GLB v2")
    chunks, off = {}, 12
    while off < len(raw):
        size, kind = struct.unpack_from("<II", raw, off)
        off += 8
        chunks[kind] = raw[off:off + size]
        off += size
    doc = json.loads(chunks[0x4E4F534A])
    binary = chunks.get(0x004E4942, b"")

    def accessor(i: int) -> np.ndarray:
        a = doc["accessors"][i]
        if "sparse" in a:
            raise ValueError("Sparse accessors are not supported")
        view = doc["bufferViews"][a["bufferView"]]
        dt, w = np.dtype(_DTYPES[a["componentType"]]), _WIDTHS[a["type"]]
        start = view.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = view.get("byteStride", w * dt.itemsize)
        arr = np.ndarray((a["count"], w), dtype=dt, buffer=binary, offset=start,
                         strides=(stride, dt.itemsize)).copy()
        if a.get("normalized"):
            arr = arr.astype(np.float64) / np.iinfo(dt).max
        return arr

    def image_bytes(tex_index: int) -> tuple[str, bytes]:
        img = doc["images"][doc["textures"][tex_index]["source"]]
        view = doc["bufferViews"][img["bufferView"]]
        start = view.get("byteOffset", 0)
        return img.get("mimeType", ""), bytes(binary[start:start + view["byteLength"]])

    P_all, F_all, UV_all, mats, textures = [], [], [], [], {}
    count = 0

    def visit(ni: int, parent: np.ndarray):
        nonlocal count
        node = doc["nodes"][ni]
        M = parent @ _node_matrix(node)
        if "mesh" in node:
            for prim in doc["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4:
                    raise ValueError("Only triangle primitives are supported")
                P = accessor(prim["attributes"]["POSITION"]).astype(np.float64)
                P = P @ M[:3, :3].T + M[:3, 3]
                idx = accessor(prim["indices"]).ravel() if "indices" in prim else np.arange(len(P))
                P_all.append(P)
                F_all.append(idx.reshape(-1, 3).astype(np.int64) + count)
                UV_all.append(accessor(prim["attributes"]["TEXCOORD_0"]).astype(np.float32)
                              if "TEXCOORD_0" in prim["attributes"] else None)
                count += len(P)
                mat = doc.get("materials", [])[prim["material"]] if "material" in prim else {}
                mats.append(mat.get("name", ""))
                if not textures:     # the first textured material provides the texture set
                    pbr = mat.get("pbrMetallicRoughness", {})
                    for slot, ref in (("basecolor", pbr.get("baseColorTexture")),
                                      ("metallic_roughness", pbr.get("metallicRoughnessTexture")),
                                      ("normal", mat.get("normalTexture"))):
                        if ref is not None:
                            textures[slot] = image_bytes(ref["index"])
        for c in node.get("children", []):
            visit(c, M)

    for ni in doc["scenes"][doc.get("scene", 0)]["nodes"]:
        visit(ni, np.eye(4))
    if not P_all:
        raise ValueError(f"{path}: no triangle geometry")
    uv = np.concatenate(UV_all) if all(u is not None for u in UV_all) else None
    return RawGLB(np.concatenate(P_all), np.concatenate(F_all), uv, textures, len(P_all), mats)


# --------------------------------------------------------------------------- canonical frame
def _face_stats(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    T = V[F]
    C = T.mean(axis=1)
    A = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    return C, A


def _weighted_median(x: np.ndarray, w: np.ndarray) -> float:
    o = np.argsort(x, kind="stable")
    cw = np.cumsum(w[o])
    return float(x[o][np.searchsorted(cw, 0.5 * cw[-1])])


def _end_slab(C, A, axis: int, sign: int, lateral: int, depth: float, bins: int = 100):
    """Area and lateral bin coverage of the slab of `depth` at the `sign` end of `axis`."""
    a = C[:, axis] * sign
    sel = a >= a.max() - depth
    lo, hi = C[:, lateral].min(), C[:, lateral].max()
    b = np.clip(((C[sel, lateral] - lo) / max(hi - lo, 1e-12) * bins).astype(int), 0, bins - 1)
    return float(A[sel].sum()), float(len(np.unique(b)) / bins)


def orientation_checks(V: np.ndarray, F: np.ndarray, slab: float) -> dict:
    """Votes on whether the lens plate of the (already yawed) mesh is at +Z.

    ``slab`` is the end-slab depth in the mesh's own units (20 mm at the provisional scale).
    Positive margins vote for +Z.
    """
    C, A = _face_stats(V, F)
    lo, hi = V.min(axis=0), V.max(axis=0)
    W = hi[0] - lo[0]
    xmid = 0.5 * (lo[0] + hi[0])
    # (a) the centre column (|x - xmid| < 8 % W: bridge, pads) sits at the lens-plate end
    col = np.abs(C[:, 0] - xmid) < 0.08 * W
    if col.any():
        zc = _weighted_median(C[col, 2], A[col])
        q_centre = (zc - lo[2]) / max(hi[2] - lo[2], 1e-12)
    else:
        q_centre = 0.5
    # (b)/(c) the lens-plate end slab is continuous across X and carries more area than the tips
    area_p, cov_p = _end_slab(C, A, 2, +1, 0, slab)
    area_m, cov_m = _end_slab(C, A, 2, -1, 0, slab)
    votes = {"centre_column_depth": float(q_centre - 0.5),
             "end_slab_coverage": float(cov_p - cov_m),
             "end_slab_area": float((area_p - area_m) / max(area_p + area_m, 1e-12))}
    plus = sum(1 for v in votes.values() if v > 0)
    # width axis: of the four horizontal ends, the lens plate (+Z or -Z) carries the most area
    area_xp, _ = _end_slab(C, A, 0, +1, 2, slab)
    area_xm, _ = _end_slab(C, A, 0, -1, 2, slab)
    ends = {"+Z": area_p, "-Z": area_m, "+X": area_xp, "-X": area_xm}
    return {"votes": votes, "votes_for_plus_z": plus, "plate_at_plus_z": plus >= 2,
            "unanimous": plus in (0, 3), "centre_column_q": float(q_centre),
            "end_slab_coverage": {"+Z": cov_p, "-Z": cov_m},
            "end_area_share": {k: float(v / max(sum(ends.values()), 1e-12)) for k, v in ends.items()},
            "width_axis_is_x": max(ends, key=ends.get) in ("+Z", "-Z")}


def up_check(V: np.ndarray, F: np.ndarray, front_z: float, slab: float) -> dict:
    """+Y up cues (reported, never corrected): the bridge sits high on the front piece (the nose
    cut-out is below it), and the temples leave the front above its mid-height."""
    C, A = _face_stats(V, F)
    lo, hi = V.min(axis=0), V.max(axis=0)
    W = hi[0] - lo[0]
    xmid = 0.5 * (lo[0] + hi[0])
    in_front = C[:, 2] >= front_z - slab
    fy_lo, fy_hi = C[in_front, 1].min(), C[in_front, 1].max()
    fh = max(fy_hi - fy_lo, 1e-12)
    out = {"note": "bridge_height_q: area-median height of the front piece's centre column (|x| < 8 % W) "
                   "in [0, 1] of the front height (> 0.5 = +Y up); temple_height_rel_front: temple "
                   "median height (30-80 mm behind the front) minus the front mid-height, in front heights"}
    col = in_front & (np.abs(C[:, 0] - xmid) < 0.08 * W)
    out["bridge_height_q"] = float((_weighted_median(C[col, 1], A[col]) - fy_lo) / fh) if col.any() else None
    sel = (np.abs(C[:, 0] - xmid) > 0.3 * W) & (C[:, 2] < front_z - 1.5 * slab) & (C[:, 2] > front_z - 4 * slab)
    out["temple_height_rel_front"] = (float((_weighted_median(C[sel, 1], A[sel]) - 0.5 * (fy_lo + fy_hi)) / fh)
                                      if sel.any() else None)
    out["passed"] = None if out["bridge_height_q"] is None else bool(out["bridge_height_q"] > 0.5)
    return out


def width_profile(V: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(d, x_min, x_max): X extremes of every vertex within depth d of the front-most vertex.

    Evaluate at any depth q with ``_width_at``; exact (a step function over the vertices).
    """
    o = np.argsort(-V[:, 2], kind="stable")
    d = V[o[0], 2] - V[o, 2]
    x = V[o, 0]
    return d, np.minimum.accumulate(x), np.maximum.accumulate(x)


def _width_at(profile, q: np.ndarray) -> np.ndarray:
    d, xmin, xmax = profile
    i = np.clip(np.searchsorted(d, q, side="right") - 1, 0, len(d) - 1)
    return xmax[i] - xmin[i]


def front_piece(V: np.ndarray, scale: float, profile=None, depth: float | None = None) -> dict:
    """The FRONT PIECE of the canonical mesh V (any units; ``scale`` = mm per V unit).

    Contract rule: geometry within 20 mm of the front-most depth. That rule is ill-posed for a
    wrapped front (sport shields), whose endpieces sit 40-60 mm behind the front-most point: the
    20 mm slab then holds only the centre of the shield and the width fixed point runs away
    (Oakley 328 mm wide, INVU 247 mm at the literal rule). So the slab starts at 20 mm and is
    EXTENDED backward while the lateral outline still wraps outward, i.e. while the width grows by
    more than 2 tan(10 deg) per unit depth over the next 10 mm. For a flat front (the width has
    plateaued by 20 mm) this is exactly the contract rule.
    """
    prof = width_profile(V) if profile is None else profile
    z_front = float(V[:, 2].max())
    total = float(prof[0][-1])
    slab = FRONT_SLAB_MM / scale
    if depth is None:
        window = FRONT_KNEE_WINDOW_MM / scale
        step = FRONT_KNEE_STEP_MM / scale
        grid = slab + step * np.arange(int(math.ceil(max(total - slab, 0) / step)) + 1)
        growth = _width_at(prof, grid + window) - _width_at(prof, grid)
        ok = np.nonzero(growth < 2.0 * math.tan(math.radians(FRONT_KNEE_DEG)) * window)[0]
        depth = float(grid[ok[0]]) if len(ok) else total
    sel = V[:, 2] >= z_front - depth
    x0, x1 = float(V[sel, 0].min()), float(V[sel, 0].max())
    return {"z_front": z_front, "depth": depth, "depth_mm": depth * scale, "x0": x0, "x1": x1, "mask": sel,
            "extended": bool(depth > slab * (1 + 1e-9)),
            "width_at_20mm": float(_width_at(prof, np.array([slab]))[0]),
            "width_total": float(np.ptp(V[:, 0]))}


def fit_scale(V: np.ndarray, front_width_mm: float, iterations: int = 100) -> tuple[float, float, list[float]]:
    """Fixed point s = W / width(front piece at scale s); the slab depth (mm) depends on s.

    Returns (scale, front depth in V units, history). The width is a step function of s, so the
    iteration ends on an exact fixed point or on a short cycle; a cycle resolves to the member with
    the widest front piece (deterministic), and the scale is then set from that depth so the
    front piece is exactly ``front_width_mm`` wide.
    """
    prof = width_profile(V)
    s = front_width_mm / float(np.ptp(V[:, 0]))
    history, depths, widths = [s], [], []
    for _ in range(iterations):
        fp = front_piece(V, s, prof)
        depths.append(fp["depth"])
        widths.append(fp["x1"] - fp["x0"])
        s_new = front_width_mm / widths[-1]
        seen = [k for k, h in enumerate(history) if abs(s_new - h) <= 1e-12 * h]
        history.append(s_new)
        if seen:                       # fixed point (seen == [last]) or a cycle
            k = max(range(seen[0], len(widths)), key=lambda i: (widths[i], -i))
            return front_width_mm / widths[k], depths[k], history
        s = s_new
    raise RuntimeError(f"Front-piece scale did not converge: {history[-4:]}")


def canonicalize(P: np.ndarray, F: np.ndarray, front_width_mm: float) -> dict:
    """Rotation (yaw -90, verified +Z front) and front-piece scale of raw positions P."""
    V1 = P @ R_YAW_M90.T
    provisional = front_width_mm / float(np.ptp(V1[:, 0]))
    checks = orientation_checks(V1, F, FRONT_SLAB_MM / provisional)
    R = R_YAW_M90
    if not checks["plate_at_plus_z"]:
        R = R_FLIP @ R_YAW_M90
    V2 = P @ R.T
    s, depth, history = fit_scale(V2, front_width_mm)
    fp = front_piece(V2, s, depth=depth)
    after = orientation_checks(V2, F, FRONT_SLAB_MM / s)
    up = up_check(V2, F, fp["z_front"], FRONT_SLAB_MM / s)
    M = np.eye(4)
    M[:3, :3] = s * R
    return {"R": R, "scale": s, "raw_to_model": M, "flipped": not checks["plate_at_plus_z"],
            "checks_before_flip": checks, "checks_final": after, "up_check": up,
            "scale_iterations": history, "front_vertex_count": int(fp["mask"].sum()),
            "front_piece": {"depth_mm": fp["depth_mm"], "depth_raw": depth, "extended_past_20mm": fp["extended"],
                            "width_at_20mm_mm": fp["width_at_20mm"] * s, "total_width_mm": fp["width_total"] * s,
                            "literal_rule_scale_note": "the literal 20 mm rule has no stable fixed point when extended"}}


# --------------------------------------------------------------------------- decimation
def decimate(V: np.ndarray, F: np.ndarray, target: int = DECIMATED_FACES) -> tuple[np.ndarray, np.ndarray, dict]:
    """Merge coincident positions (UV seams), then open3d quadric decimation (deterministic)."""
    import open3d as o3d
    m = o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(np.asarray(V, np.float64)),
                                  o3d.utility.Vector3iVector(np.asarray(F, np.int32)))
    m.remove_duplicated_vertices()
    merged = len(m.vertices)
    d = m.simplify_quadric_decimation(target_number_of_triangles=int(target))
    d.remove_degenerate_triangles()
    d.remove_unreferenced_vertices()
    Vd = np.asarray(d.vertices, np.float64).astype(np.float32)
    Fd = np.asarray(d.triangles, np.int32)
    return Vd, Fd, {"merged_vertices": merged, "vertices": int(len(Vd)), "faces": int(len(Fd)),
                    "edge_manifold": bool(d.is_edge_manifold()), "watertight": bool(d.is_watertight())}


# --------------------------------------------------------------------------- loading for later stages
@dataclass
class Generator:
    product: str
    run: str
    root: Path
    result: dict
    V: np.ndarray
    F: np.ndarray
    UV: np.ndarray | None
    Vd: np.ndarray
    Fd: np.ndarray
    frame: NormFrame

    def scene(self, decimated: bool = True) -> raster.RasterScene:
        """Cached ray-casting scene; decimated (~24k faces) for camera fitting, full for measurement."""
        return raster.get_scene(self.Vd, self.Fd, self.frame) if decimated else raster.get_scene(self.V, self.F, self.frame)

    def texture(self, slot: str = "basecolor") -> np.ndarray | None:
        name = {"basecolor": "basecolor.jpg", "metallic_roughness": "metallic_roughness.png", "normal": "normal.png"}[slot]
        p = self.root / name
        return np.asarray(Image.open(p).convert("RGB")) if p.exists() else None

    def colour(self, face_id: np.ndarray, bary: np.ndarray, texture: np.ndarray) -> np.ndarray:
        """Base colour (float32 0..255) at full-mesh hits (face_id >= 0, open3d barycentrics)."""
        return sample_texture(texture, interpolate_uv(self.UV, self.F, face_id, bary))


def load(product: str, run: str = "m1") -> Generator:
    sd = stage_dir(run, product, STAGE)
    if not sd.done():
        raise FileNotFoundError(f"S1 artifacts missing for {product}/{run}: run bsa.generator first")
    result, arrays = sd.load()
    return Generator(product, run, sd.root, result, arrays["V"], arrays["F"], arrays.get("UV"),
                     arrays["Vd"], arrays["Fd"], NormFrame.from_dict(result["frame"]))


def interpolate_uv(UV: np.ndarray, F: np.ndarray, face_id: np.ndarray, bary: np.ndarray) -> np.ndarray:
    f = np.asarray(face_id).ravel()
    b = np.asarray(bary, np.float64).reshape(-1, 2)
    T = UV[F[f]].astype(np.float64)
    return (1 - b[:, 0] - b[:, 1])[:, None] * T[:, 0] + b[:, 0, None] * T[:, 1] + b[:, 1, None] * T[:, 2]


def sample_texture(tex: np.ndarray, uv: np.ndarray) -> np.ndarray:
    """Bilinear sample of an HxWxC texture at glTF UVs (v down, repeat wrap); float32."""
    H, W = tex.shape[:2]
    u = np.mod(uv[:, 0], 1.0) * W - 0.5
    v = np.mod(uv[:, 1], 1.0) * H - 0.5
    x0, y0 = np.floor(u).astype(int), np.floor(v).astype(int)
    fx, fy = (u - x0)[:, None], (v - y0)[:, None]
    x0c, x1c = np.mod(x0, W), np.mod(x0 + 1, W)
    y0c, y1c = np.mod(y0, H), np.mod(y0 + 1, H)
    t = tex.reshape(H, W, -1).astype(np.float32)
    return (t[y0c, x0c] * (1 - fx) * (1 - fy) + t[y0c, x1c] * fx * (1 - fy)
            + t[y1c, x0c] * (1 - fx) * fy + t[y1c, x1c] * fx * fy)


# --------------------------------------------------------------------------- stage
def _save_textures(glb: RawGLB, root: Path) -> dict:
    out = {}
    for slot, (mime, data) in glb.textures.items():
        if slot == "basecolor":
            name = "basecolor.jpg"
            if mime == "image/jpeg":
                (root / name).write_bytes(data)          # native bytes, no re-encode
            else:
                Image.open(io.BytesIO(data)).convert("RGB").save(root / name, quality=95)
        else:
            name = f"{slot}.png"
            if mime == "image/png":
                (root / name).write_bytes(data)
            else:
                Image.open(io.BytesIO(data)).convert("RGB").save(root / name)
        with Image.open(root / name) as im:
            out[slot] = {"file": name, "source_mime": mime, "size": list(im.size), "bytes": (root / name).stat().st_size}
    return out


def _silhouette_agreement(full: raster.RasterScene, dec: raster.RasterScene, frame: NormFrame) -> dict:
    """IoU and symmetric mean contour distance (mm) of full vs decimated silhouettes at 4 px/mm."""
    from scipy import ndimage
    out = {}
    shape = (int(1.15 * frame.extent * SHEET_PX_PER_MM),) * 2
    for name, (yaw, pitch) in {"front": (0, 0), "side": (90, 0), "top": (0, 90), "oblique": (35, 15)}.items():
        cam = raster.view_camera(frame, yaw, pitch, SHEET_PX_PER_MM, shape)
        a = full.render(cam, shape)["mask"]
        b = dec.render(cam, shape)["mask"]
        iou = np.count_nonzero(a & b) / max(np.count_nonzero(a | b), 1)
        ea, eb = a & ~ndimage.binary_erosion(a), b & ~ndimage.binary_erosion(b)
        da, db = ndimage.distance_transform_edt(~ea), ndimage.distance_transform_edt(~eb)
        d = np.concatenate([db[ea], da[eb]]) / SHEET_PX_PER_MM
        out[name] = {"iou": float(iou), "contour_mean_mm": float(d.mean()), "contour_p95_mm": float(np.quantile(d, 0.95))}
    return out


def run(product: str, run: str = "m1", force: bool = False) -> dict:
    sd = stage_dir(run, product, STAGE)
    if sd.done() and not force:
        return sd.load()[0]
    prod: Product = PRODUCTS[product]
    timings = {}
    t0 = t = time.perf_counter()

    glb = read_glb(prod.generation_glb)
    timings["read_glb"] = time.perf_counter() - t
    t = time.perf_counter()

    canon = canonicalize(glb.positions, glb.faces, prod.front_width_mm)
    V = (glb.positions @ (canon["scale"] * canon["R"]).T).astype(np.float32)
    F = glb.faces.astype(np.int32)
    V64 = V.astype(np.float64)
    lo, hi = V64.min(axis=0), V64.max(axis=0)
    frame = NormFrame(tuple(float(c) for c in (lo + hi) / 2), float(np.max(hi - lo)))
    fp = front_piece(V64, 1.0, depth=canon["front_piece"]["depth_mm"])
    z_front, x0, x1 = fp["z_front"], fp["x0"], fp["x1"]
    timings["canonicalize"] = time.perf_counter() - t
    t = time.perf_counter()

    Vd, Fd, dec_info = decimate(V64, F)
    timings["decimate"] = time.perf_counter() - t
    t = time.perf_counter()

    arrays = {"V": V, "F": F, "Vd": Vd, "Fd": Fd}
    if glb.uv is not None:
        arrays["UV"] = glb.uv.astype(np.float32)
    textures = _save_textures(glb, sd.root)
    timings["save_textures"] = time.perf_counter() - t
    t = time.perf_counter()

    full_scene = raster.get_scene(V64, F, frame)
    dec_scene = raster.get_scene(Vd, Fd, frame)
    agreement = _silhouette_agreement(full_scene, dec_scene, frame)
    timings["decimation_check"] = time.perf_counter() - t

    flags = []
    cf = canon["checks_final"]
    if canon["flipped"]:
        flags.append("flipped_x_z")
    if not canon["checks_before_flip"]["unanimous"]:
        flags.append("orientation_votes_split")
    if not cf["plate_at_plus_z"]:
        flags.append("plate_not_at_plus_z_after_flip")
    if not cf["width_axis_is_x"]:
        flags.append("width_axis_suspect")
    if canon["up_check"].get("passed") is False:
        flags.append("up_check_failed")
    if min(v["iou"] for v in agreement.values()) < 0.97:
        flags.append("decimation_silhouette_loss")
    if fp["extended"]:
        flags.append("front_wraps_past_20mm_slab")
    if glb.uv is None:
        flags.append("no_uv")
    if glb.primitives != 1:
        flags.append(f"primitives_{glb.primitives}")

    ext = hi - lo
    result = {
        "stage": STAGE, "product": product, "run": run,
        "source": str(prod.generation_glb), "source_sha256": sha256_file(prod.generation_glb),
        "frame": frame.to_dict(),
        "raw_to_model": canon["raw_to_model"].tolist(),
        "scale_mm_per_raw": canon["scale"],
        "front_z_mm": z_front,
        "front_width_mm": x1 - x0,
        "front_x_range_mm": [x0, x1],
        "front_slab_mm": FRONT_SLAB_MM,
        "front_depth_mm": fp["depth_mm"],
        "front_piece": {"rule": "20 mm slab, extended while the lateral outline wraps > 10 deg/side over 10 mm",
                        "depth_mm": fp["depth_mm"], "extended_past_20mm": fp["extended"],
                        "width_at_20mm_mm": fp["width_at_20mm"], "total_x_extent_mm": fp["width_total"]},
        "target_front_width_mm": prod.front_width_mm,
        "bbox_mm": {"min": lo.tolist(), "max": hi.tolist(), "extent": ext.tolist()},
        "depth_to_width": float(ext[2] / (x1 - x0)),
        "orientation_checks": {"yaw_prior": "(x,y,z)->(-z,y,x)", "flipped": canon["flipped"],
                               "before_flip": canon["checks_before_flip"], "final": cf,
                               "up": canon["up_check"],
                               "scale_iterations": canon["scale_iterations"]},
        "counts": {"vertices": int(len(V)), "faces": int(len(F)), "uv": glb.uv is not None,
                   "primitives": glb.primitives, "front_piece_vertices": canon["front_vertex_count"],
                   "decimated": dec_info},
        "decimation_agreement": agreement,
        "textures": textures,
        "flags": flags,
    }
    t = time.perf_counter()
    sd.save(result, arrays)       # arrays first; result.json written last marks completion
    timings["save_arrays"] = time.perf_counter() - t
    t = time.perf_counter()
    gen = Generator(product, run, sd.root, result, V, F, arrays.get("UV"), Vd, Fd, frame)
    make_sheet(gen, sd.root / "sheet.png")
    timings["sheet"] = time.perf_counter() - t
    timings["total"] = time.perf_counter() - t0
    result["timings_s"] = {k: round(v, 3) for k, v in timings.items()}
    sd.save(result)
    return result


# --------------------------------------------------------------------------- proof sheet
def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def shaded_render(gen: Generator, camera: Camera, shape: tuple[int, int], texture: np.ndarray | None,
                  background: int = 255) -> tuple[np.ndarray, dict]:
    """Textured (base colour x Lambert) render of the full generator; returns (RGB uint8, raw render)."""
    r = gen.scene(decimated=False).render(camera, shape, want_bary=True, want_normal=True)
    img = np.full(shape + (3,), background, np.uint8)
    m = r["mask"]
    if m.any():
        toward = raster.camera_basis(camera)[2]
        lam = np.abs(r["normal"][m] @ toward)
        shade = (0.45 + 0.55 * lam)[:, None]
        if texture is not None and gen.UV is not None:
            col = gen.colour(r["face_id"][m], r["bary"][m], texture)
        else:
            col = np.full((int(m.sum()), 3), 180.0, np.float32)
        img[m] = np.clip(col * shade, 0, 255).astype(np.uint8)
    return img, r


def _axis_gizmo(draw: ImageDraw.ImageDraw, camera: Camera, origin: tuple[int, int], font, length: int = 60):
    right, up, toward = raster.camera_basis(camera)
    colours = {"X": (220, 40, 40), "Y": (30, 160, 30), "Z": (40, 70, 230)}
    for k, name in enumerate("XYZ"):
        e = np.zeros(3)
        e[k] = 1.0
        dx, dy = float(e @ right), -float(e @ up)
        if math.hypot(dx, dy) < 0.3:
            sign = "+" if e @ toward > 0 else "-"
            draw.text((origin[0] + length + 40, origin[1] - 10), f"{sign}{name} {'toward' if sign == '+' else 'away from'} viewer",
                      fill=colours[name], font=font)
            continue
        end = (origin[0] + dx * length, origin[1] + dy * length)
        draw.line([origin, end], fill=colours[name], width=4)
        draw.ellipse([end[0] - 5, end[1] - 5, end[0] + 5, end[1] + 5], fill=colours[name])
        draw.text((end[0] + 6 * np.sign(dx) - (22 if dx < 0 else 0), end[1] + 6 * np.sign(dy) - 8), f"+{name}",
                  fill=colours[name], font=font)


def _tile(img: np.ndarray, title: str, camera: Camera | None, font, footer: int = 96,
          notes: tuple[str, ...] = ()) -> np.ndarray:
    """Render tile with a title and a footer strip carrying the model-axis gizmo (never over the model)."""
    h, w = img.shape[:2]
    im = Image.new("RGB", (w, h + footer), (255, 255, 255))
    im.paste(Image.fromarray(img), (0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle([0, h, w - 1, h + footer - 1], fill=(246, 246, 246))
    d.text((10, 8), title, fill=(0, 0, 0), font=font)
    if camera is not None:
        _axis_gizmo(d, camera, (50, h + footer // 2), font, length=32)
    for k, note in enumerate(notes):
        d.text((320, h + 8 + 22 * k), note, fill=(0, 0, 0), font=font)
    d.rectangle([0, 0, w - 1, h + footer - 1], outline=(200, 200, 200))
    return np.asarray(im)


def make_sheet(gen: Generator, path: Path) -> None:
    """Orthographic proof renders: +Z front / +Y up / +X right and the front-piece width."""
    res, frame = gen.result, gen.frame
    ext = np.asarray(res["bbox_mm"]["extent"])
    ppm = SHEET_PX_PER_MM
    side = int(math.ceil(1.3 * frame.extent * ppm))
    shape = (side, side)
    tex = gen.texture("basecolor")
    font, small, big = _font(20), _font(17), _font(26)
    mag = (255, 0, 170)
    z_front = res["front_z_mm"]
    x0, x1 = res["front_x_range_mm"]
    yc = frame.center[1]
    y_lo, y_hi = res["bbox_mm"]["min"][1], res["bbox_mm"]["max"][1]
    tiles = {}
    views = {"front": ("FRONT  camera yaw 0, pitch 0 (from +Z)", 0, 0),
             "back": ("BACK  camera yaw 180 (from -Z)", 180, 0),
             "side": ("SIDE  camera yaw +90 (from +X)", 90, 0),
             "top": ("TOP  camera pitch +90 (from +Y)", 0, 90),
             "oblique": ("OBLIQUE  yaw +35, pitch +15", 35, 15)}
    for key, (title, yaw, pitch) in views.items():
        cam = raster.view_camera(frame, yaw, pitch, ppm, shape)
        img, _ = shaded_render(gen, cam, shape, tex)
        im = Image.fromarray(img)
        d = ImageDraw.Draw(im)
        notes: tuple[str, ...] = ()
        if key in ("front", "back"):
            # front-piece X extremes as vertical lines and a dimension line under the model
            for xv in (x0, x1):
                a, b = project_mm(np.array([[xv, y_lo - 6, z_front], [xv, y_hi + 4, z_front]]), cam, frame)
                d.line([tuple(a), tuple(b)], fill=mag, width=2)
            a, b = project_mm(np.array([[x0, y_lo - 4, z_front], [x1, y_lo - 4, z_front]]), cam, frame)
            d.line([tuple(a), tuple(b)], fill=mag, width=2)
            d.text((min(a[0], b[0]) + 10, a[1] + 6),
                   f"front piece width {x1 - x0:.2f} mm (target {res['target_front_width_mm']:.0f})", fill=mag, font=font)
            if key == "front":
                notes = ("lens plate faces the viewer (+Z)",)
        elif key in ("side", "top"):
            # the front-piece slab: front-most depth and the 20 mm cut behind it
            depth = res["front_depth_mm"]
            for k, (zv, lab) in enumerate(((z_front, "front-most depth"), (z_front - depth, f"front piece -{depth:.1f} mm"))):
                if key == "side":
                    a, b = project_mm(np.array([[x1, y_hi + 8, zv], [x1, y_lo - 8, zv]]), cam, frame)
                    d.line([tuple(a), tuple(b)], fill=mag, width=2)
                    d.text((a[0] + 4, a[1] - 22 - 22 * k), lab, fill=mag, font=small)
                else:
                    a, b = project_mm(np.array([[x0 - 8, yc, zv], [x1 + 8, yc, zv]]), cam, frame)
                    d.line([tuple(a), tuple(b)], fill=mag, width=2)
                    d.text((b[0] - 150, b[1] + (4 if k == 0 else -22)), lab, fill=mag, font=small)
            notes = ("front (+Z) at the left" if key == "side" else "front (+Z) at the bottom",)
        tiles[key] = _tile(np.asarray(im), title, cam, font, notes=notes)
    # front photo for handedness comparison, and decimated vs full silhouettes
    photo = load_photo(PRODUCTS[gen.product], "front")
    ph = Image.fromarray(photo)
    ph.thumbnail((side, side))
    pim = Image.new("RGB", (side, side), (255, 255, 255))
    pim.paste(ph, ((side - ph.size[0]) // 2, (side - ph.size[1]) // 2))
    tiles["photo"] = _tile(np.asarray(pim), "FRONT PHOTO (left/right cues)", None, font,
                           notes=(f"{photo.shape[1]} x {photo.shape[0]} px",))
    for key, yaw, pitch in (("front", 0, 0), ("side", 90, 0)):
        cam = raster.view_camera(frame, yaw, pitch, ppm, shape)
        a = gen.scene(False).render(cam, shape)["mask"]
        b = gen.scene(True).render(cam, shape)["mask"]
        ov = np.full(shape + (3,), 255, np.uint8)
        ov[a & b] = (150, 150, 150)
        ov[a & ~b] = (230, 30, 30)
        ov[~a & b] = (30, 60, 230)
        ag = res["decimation_agreement"][key]
        tiles["dec_" + key] = _tile(ov, f"DECIMATED vs FULL, {key}", cam, font,
                                    notes=(f"{res['counts']['decimated']['faces']} faces: IoU {ag['iou']:.4f}",
                                           f"contour mean {ag['contour_mean_mm']:.3f} mm, p95 {ag['contour_p95_mm']:.3f} mm",
                                           "grey both / red full only / blue dec. only"))
    rows = [np.concatenate([tiles[k] for k in ("photo", "front", "back", "dec_front")], axis=1),
            np.concatenate([tiles[k] for k in ("side", "top", "oblique", "dec_side")], axis=1)]
    grid = np.concatenate(rows, axis=0)
    header = Image.new("RGB", (grid.shape[1], 76), (235, 235, 235))
    hd = ImageDraw.Draw(header)
    oc = res["orientation_checks"]
    votes = oc["before_flip"]["votes"]
    up = oc["up"]
    bq = up.get("bridge_height_q")
    tq = up.get("temple_height_rel_front")
    hd.text((10, 6), f"{gen.product}  S1 canonical generator  |  {res['scale_mm_per_raw']:.3f} mm/raw  |  bbox "
                     f"{ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} mm  |  flipped: {oc['flipped']}  |  "
                     f"flags: {', '.join(res['flags']) or 'none'}", fill=(0, 0, 0), font=big)
    hd.text((10, 44), "+Z votes before flip: " + ", ".join(f"{k} {v:+.3f}" for k, v in votes.items())
            + f"  |  +Y: bridge q {bq if bq is None else round(bq, 2)} (>0.5 = up), temples {tq if tq is None else round(tq, 2)}"
            + f"  |  {ppm:g} px/mm orthographic, full mesh, base colour x Lambert", fill=(0, 0, 0), font=small)
    sheet = np.concatenate([np.asarray(header), grid], axis=0)
    Image.fromarray(sheet).save(path, optimize=True)


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="BSA S1: canonical generator")
    ap.add_argument("--run", default="m1")
    ap.add_argument("--product", action="append")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args(argv)
    for p in a.product or list(PRODUCTS):
        r = run(p, a.run, a.force)
        print(p, json.dumps({k: r[k] for k in ("scale_mm_per_raw", "front_width_mm", "front_z_mm", "flags", "timings_s")}))


if __name__ == "__main__":
    main()
