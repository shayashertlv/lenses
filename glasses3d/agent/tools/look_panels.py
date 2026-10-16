"""Photo/AR pairs and geometry-selected native details; no appearance verdict or colour correction.

The part-id pass supplies visible coverage, not semantics: all optical meshes share magenta,
and a single front mesh can contain its face, outer wall and inner rim. First-hit GLB triangles
therefore supplement those IDs. Selection thresholds are reported, never quality gates.
"""
from dataclasses import dataclass, field
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_dilation, binary_erosion, label

from ...g3d_host import glb, lens_id, pose as PO

DETAIL_SIDE = 500
HIGH_PATCH_BUDGET = 2500
HIGH_MAX_SIDE = 65535
DETAIL_KINDS = ("outer_corner", "inner_rim", "lens_mark", "hinge_root", "arm_tip")


def _scene(vertices, faces):
    import open3d as o3d
    scene = o3d.t.geometry.RaycastingScene(nthreads=1)
    scene.add_triangles(o3d.core.Tensor(np.asarray(vertices, np.float32)),
                        o3d.core.Tensor(np.asarray(faces, np.uint32)))
    return scene


def _tensor(values):
    import open3d as o3d
    return o3d.core.Tensor(np.asarray(values, np.float32))


@dataclass
class Geometry:
    vertices: np.ndarray
    faces: np.ndarray
    face_parts: np.ndarray
    parts: list[dict]
    glass: np.ndarray
    markings: np.ndarray
    front: np.ndarray
    temples: np.ndarray
    scene: object = field(repr=False)
    glass_scene: object = field(default=None, repr=False)


def load_geometry(path):
    """Load immutable exported metres; use exact roles/geometry, never product or object-name rules."""
    model = glb.load(path)
    refs = []
    for i, p in enumerate(model.primitives):
        material = model.material(p.material_index) or {}
        refs.append(lens_id.PrimitiveRef(str(i), p.node_name, model.nodes[p.node_index].get("extras"),
            model.meshes[p.mesh_index].get("extras"), material.get("name"), material.get("extras"), material))
    decisions, _ = lens_id.classify(refs)
    vertices, faces, owners, parts, offset = [], [], [], [], 0
    for i, p in enumerate(model.primitives):
        if p.compressed or p.error:
            raise ValueError(f"cannot inspect exported geometry: {p.compressed or p.error}")
        if not len(p.triangles):
            continue
        material = model.material(p.material_index) or {}
        extras = [material.get("extras") or {}, model.meshes[p.mesh_index].get("extras") or {},
                  model.nodes[p.node_index].get("extras") or {}]
        role = next((str(e[k]).strip().lower() for e in extras for k in ("partRole", "part_role")
                     if isinstance(e.get(k), str) and e[k].strip()), "")
        triangles = p.positions[p.triangles]
        area = float(np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                             triangles[:, 2] - triangles[:, 0]), axis=1).sum() / 2)
        transmission = float(((material.get("extensions") or {}).get("KHR_materials_transmission") or {}).get("transmissionFactor", 0))
        parts.append({"name": p.node_name, "node_index": p.node_index, "primitive_index": p.primitive_index,
                      "lens": decisions[str(i)].is_lens, "transmitting": transmission > 0,
                      "role": role, "area_m2": area, "bounds": np.array([p.positions.min(0), p.positions.max(0)]),
                      "vertices": p.positions})
        vertices.append(p.positions)
        faces.append(p.triangles + offset)
        owners.append(np.full(len(p.triangles), len(parts) - 1, int))
        offset += len(p.positions)
    if not faces:
        raise ValueError("export has no inspectable triangles")
    V, F, owner = np.vstack(vertices), np.vstack(faces), np.concatenate(owners)
    areas = np.array([p["area_m2"] for p in parts])
    optical = np.array([p["lens"] for p in parts])
    # Tiny optical-tagged letters must not define the glass envelope. A physically opaque lens
    # remains eligible as a large optical part: transmission is NOT a prerequisite for glass.
    largest = float(areas[optical].max()) if optical.any() else 0.
    glass = optical & (areas >= largest * .1) if largest else np.zeros(len(parts), bool)
    gs = _scene(V, F[glass[owner]]) if glass.any() else None
    marks = np.zeros(len(parts), bool)
    if gs is not None:
        glass_area = float(areas[glass].sum())
        for i, p in enumerate(parts):
            if glass[i] or p["transmitting"] or p["area_m2"] > .02 * glass_area:
                continue
            distances = gs.compute_distance(_tensor(p["vertices"]), nthreads=1).numpy()
            marks[i] = bool(len(distances) and np.max(distances) <= .0003)
    # Logos/hardware may inherit a supporting temple role; they are not whole arms.
    temples = np.array([p["role"].startswith("temple") and np.ptp(p["vertices"][:, 2]) > .04 for p in parts])
    if not temples.any():
        # Positional fallback for unlabelled long arms, independent of names and vertex density.
        temples = np.array([not optical[i] and np.ptp(p["vertices"][:, 2]) > .04 and
            (p["bounds"][0, 0] > 0 or p["bounds"][1, 0] < 0) for i, p in enumerate(parts)])
    front = ~(glass | marks | temples)
    return Geometry(V, F, owner, parts, glass, marks, front, temples, _scene(V, F), gs)


def posed_geometry(geometry, pose):
    """The same frozen hinge motion as the AR camera, on a new geometry/raycast copy."""
    parsed = PO.parse_pose(pose)
    if not any(theta for theta, _ in parsed.values()):
        return geometry
    vertices, parts = [], []
    for part in geometry.parts:
        record = dict(part)
        v = part["vertices"]
        side = 1 if part["bounds"][:, 0].sum() > 0 else -1
        if part["role"] == "temple" and side in parsed and abs(part["bounds"][:, 0].sum()) > 2e-9:
            theta, axis = parsed[side]
            v = PO.rot_about_hinge(v * 1000, side, theta, axis) / 1000
            record.update(selection_bounds=part["bounds"], selection_pose=(side, theta, axis))
        record.update(vertices=v, bounds=np.array([v.min(0), v.max(0)]))
        vertices.append(v)
        parts.append(record)
    V = np.vstack(vertices)
    gs = _scene(V, geometry.faces[geometry.glass[geometry.face_parts]]) if geometry.glass.any() else None
    return Geometry(V, geometry.faces, geometry.face_parts, parts, geometry.glass, geometry.markings,
                    geometry.front, geometry.temples, _scene(V, geometry.faces), gs)


def decode_masks(ids, palette):
    """Coverage by class; shared IDs remain shared and mixed is not silently lost."""
    masks = {key: np.zeros(ids.shape[:2], bool) for key in ("lens", "frame", "mixed")}
    rgb = ids.astype(np.int16)
    for entry in palette:
        if entry.get("class") in masks:
            masks[entry["class"]] |= np.max(np.abs(rgb - np.asarray(entry["rgb"])), axis=2) <= entry.get("decode_tolerance", 0)
    masks["visible"] = np.logical_or.reduce(list(masks.values()))
    return masks


def visible_geometry(geometry, camera, visible):
    """First solid hit at the SAME explicit camera; ray normals classify surfaces, not dark pixels."""
    y, x = np.nonzero(visible)
    out = {key: np.zeros(visible.shape, bool) for key in
           ("glass", "markings", "front", "front_face", "front_wall", "inner_rim_wall", "temple_root", "temple_tip")}
    out["part"] = np.full(visible.shape, -1, np.int32)
    if not len(x):
        return out
    view = np.asarray(camera["view"], float).reshape(4, 4)
    R, t = view[:3, :3], view[:3, 3]
    directions = np.column_stack(((x - camera["cx"]) / camera["fx"],
                                  (camera["cy"] - y) / camera["fy"], -np.ones(len(x)))) @ R
    eye = -R.T @ t
    rays = np.column_stack((np.broadcast_to(eye, directions.shape), directions))
    hit = geometry.scene.cast_rays(_tensor(rays), nthreads=1)
    ids, distance = hit["primitive_ids"].numpy(), hit["t_hit"].numpy()
    good = np.isfinite(distance) & (ids < len(geometry.faces))
    if not good.any():
        return out
    y, x, ids = y[good], x[good], ids[good].astype(int)
    owner = geometry.face_parts[ids]
    point = eye + directions[good] * distance[good, None]
    normal = hit["primitive_normals"].numpy()[good]
    out["part"][y, x] = owner
    for key, selector in (("glass", geometry.glass), ("markings", geometry.markings), ("front", geometry.front)):
        out[key][y, x] = selector[owner]
    # A +Z forward asset has front/back faces with |nz| near 1 and depth walls near 0.
    # Restrict to the lens-depth neighbourhood so unlabelled remote arm pieces cannot be walls.
    front = geometry.front[owner].copy()
    if geometry.glass.any():
        bounds = np.concatenate([p["vertices"] for i, p in enumerate(geometry.parts) if geometry.glass[i]])
        front &= point[:, 2] >= bounds[:, 2].min() - .012
    face = front & (np.abs(normal[:, 2]) >= .85)
    wall = front & (np.abs(normal[:, 2]) <= .5)
    out["front_face"][y, x] = face
    out["front_wall"][y, x] = wall
    if geometry.glass_scene is not None and wall.any():
        close = geometry.glass_scene.compute_distance(_tensor(point[wall]), nthreads=1).numpy() <= .002
        wy, wx = y[wall], x[wall]
        out["inner_rim_wall"][wy[close], wx[close]] = True
    for i, p in enumerate(geometry.parts):
        if not geometry.temples[i]:
            continue
        selected_points = point
        if "selection_pose" in p:
            side, theta, axis = p["selection_pose"]
            selected_points = PO.rot_about_hinge(point * 1000, side, -theta, axis) / 1000
        lo, hi = p.get("selection_bounds", p["bounds"])[:, 2]
        span = hi - lo
        same = owner == i
        out["temple_root"][y[same & (selected_points[:, 2] >= hi - .15 * span)], x[same & (selected_points[:, 2] >= hi - .15 * span)]] = True
        out["temple_tip"][y[same & (selected_points[:, 2] <= lo + .15 * span)], x[same & (selected_points[:, 2] <= lo + .15 * span)]] = True
    return out


def _box(mask, margin=12):
    y, x = np.nonzero(mask)
    if not len(x):
        return None
    h, w = mask.shape
    return [max(0, int(x.min()) - margin), max(0, int(y.min()) - margin),
            min(w, int(x.max()) + margin + 1), min(h, int(y.max()) + margin + 1)]


def _components(mask, minimum=4):
    labels, count = label(mask)
    return [labels == i for i in range(1, count + 1) if np.count_nonzero(labels == i) >= minimum]


def _tiles(box, side=DETAIL_SIDE):
    """Cover a large detail in native overlapping tiles, never shrink it to claim native detail."""
    x0, y0, x1, y1 = box
    def starts(a, b):
        return [a] if b - a <= side else list(range(a, b - side, side - 32)) + [b - side]
    return [[x, y, min(x1, x + side), min(y1, y + side)] for y in starts(y0, y1) for x in starts(x0, x1)]


def _stats(image, mask):
    pixels = np.asarray(image)[mask]
    if not len(pixels):
        return {"status": "unavailable", "pixels": 0, "reason": "no visible pixels in this geometry-selected surface"}
    rgb = pixels.astype(float) / 255
    linear = np.where(rgb <= .04045, rgb / 12.92, ((rgb + .055) / 1.055) ** 2.4)
    Y = linear @ [.2126, .7152, .0722]
    L = np.where(Y > (6 / 29) ** 3, 116 * np.cbrt(Y) - 16, (29 / 3) ** 3 * Y)
    return {"status": "measured", "pixels": len(pixels), "median_srgb_8bit": np.median(pixels, axis=0).tolist(),
            "median_lightness_Lstar": float(np.median(L))}


def _pair(photo, drawing, box, path, title, max_side):
    a, b = photo.crop(box), drawing.crop(box)
    header, gap = 42, 8
    scale = min(1., (max_side - gap) / (a.width * 2), (max_side - header) / a.height)
    def fits(s):
        width = max(1, int(a.width * s)) * 2 + gap
        height = max(1, int(a.height * s)) + header
        return ((width + 31) // 32) * ((height + 31) // 32) <= HIGH_PATCH_BUDGET
    if not fits(scale):
        low, high = 0., scale
        for _ in range(48):
            mid = (low + high) / 2
            if fits(mid):
                low = mid
            else:
                high = mid
        scale = low
    size = (max(1, int(a.width * scale)), max(1, int(a.height * scale)))
    if size != a.size:
        a, b = a.resize(size, Image.Resampling.LANCZOS), b.resize(size, Image.Resampling.LANCZOS)
    result = Image.new("RGB", (a.width * 2 + gap, a.height + header), "#ededed")
    draw = ImageDraw.Draw(result)
    draw.text((6, 4), title, fill="black")
    draw.text((6, 23), "PHOTO", fill="black")
    draw.text((a.width + gap + 6, 23), "AR / SAME CAMERA AND PIXEL BOX", fill="black")
    result.paste(a, (0, header))
    result.paste(b, (a.width + gap, header))
    result.save(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_px": list(result.size),
            "crop_box": box, "pair_scale": scale, "native_detail": scale == 1.,
            "patches_32px": ((result.width + 31) // 32) * ((result.height + 31) // 32)}


def build_panels(row, palette, export_glb_or_geometry, output_dir, *, max_side=HIGH_MAX_SIDE, surface_only=False):
    """Build a full pair and visible native detail pairs, leaving worn-front delivery to the caller.

    detail:high's 2500 32px-patch budget includes labels/gaps (round each dimension up).
    max_side may impose a smaller host limit. Native means the explicit-camera capture grid;
    source-photo coordinates and its resampling scale are recorded.
    """
    if max_side < 128:
        raise ValueError("pair limit must allow readable images")
    geometry = export_glb_or_geometry if isinstance(export_glb_or_geometry, Geometry) else load_geometry(export_glb_or_geometry)
    geometry = posed_geometry(geometry, row["camera"].get("temple_pose"))
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    photo = Image.open(row["photo_path"]).convert("RGB")
    drawing = Image.open(row["drawing_path"]).convert("RGB")
    ids = np.asarray(Image.open(row["part_id_path"]).convert("RGB"))
    if photo.size != drawing.size or ids.shape[:2] != (photo.height, photo.width):
        raise ValueError("photo, drawing and part-id pixels must share a camera grid")
    masks = decode_masks(ids, palette)
    selected = visible_geometry(geometry, row["camera"], masks["visible"])
    # Existing frozen photo/model union remains authoritative for the full pair, including mismatches.
    box = row.get("crop_box") or [0, 0, photo.width, photo.height]
    full = _pair(photo, drawing, box, directory / "full-pair.png", f"{row['ref']}: full photo / AR", max_side)
    full.update(view=f"{row['ref']}: full photo / AR", kind="look_full_pair", ref=row["ref"])
    images, details = ([] if surface_only else [full]), []
    kinds = ("facet_flatness", "shading_seam", "lens_ripple") if surface_only else DETAIL_KINDS
    candidates = {key: [] for key in (*DETAIL_KINDS, *kinds)}
    glasses = _components(selected["glass"], 16)
    for component in glasses:
        rim = binary_dilation(component, iterations=8) & selected["front"]
        if rim.any():
            candidates["inner_rim"].append((_box(component | rim, 16), "visible glass and adjacent front; native complete rim coverage"))
    # Outer corners are chosen from actual front/background boundaries near each visible lens.
    outer = selected["front"] & binary_dilation(~masks["visible"], iterations=2)
    for component in glasses:
        y, x = np.nonzero(component)
        cx, cy = float(x.mean()), float(y.mean())
        near = outer & binary_dilation(component, iterations=max(16, int((x.max() - x.min()) * .35)))
        oy, ox = np.nonzero(near)
        if len(ox):
            i = int(np.argmax(((ox - cx) / max(1, np.ptp(x))) ** 2 + ((oy - cy) / max(1, np.ptp(y))) ** 2))
            radius = min(DETAIL_SIDE // 2, max(64, int(np.ptp(x) * .35)))
            candidates["outer_corner"].append(([max(0, int(ox[i]) - radius), max(0, int(oy[i]) - radius),
                min(photo.width, int(ox[i]) + radius + 1), min(photo.height, int(oy[i]) + radius + 1)],
                "extreme visible front/background boundary beside a visible lens"))
    for kind, maskkey in (("lens_mark", "markings"), ("hinge_root", "temple_root"), ("arm_tip", "temple_tip")):
        # Keep one part together: disconnected letter strokes must not become dozens of tiny images.
        for part in np.unique(selected["part"][selected[maskkey]]):
            mask = selected[maskkey] & (selected["part"] == part)
            box = _box(mask, 24)
            if box:
                candidates[kind].append((box, {"lens_mark": "small non-transmitting geometry within 0.3 mm of glass; area <=2% of glass",
                    "hinge_root": "visible proximal 15% of an authored/geometrically inferred arm's depth span",
                    "arm_tip": "visible distal 15% of an authored/geometrically inferred arm's depth span"}[kind]))
    if surface_only:
        for component in glasses:
            candidates["lens_ripple"].append((_box(component, 16), "visible lens surface; inspect moving highlights for coarse tessellation or normal jumps"))
        for part in np.unique(selected["part"]):
            if part < 0:
                continue
            mask = selected["part"] == part
            if geometry.temples[part]:
                candidates["facet_flatness"].append((_box(mask, 16), "visible temple surface; inspect planes and unintended internal lines"))
            if geometry.front[part]:
                candidates["shading_seam"].append((_box(mask, 16), "visible front; inspect top corners and shading seams"))
    for kind in kinds:
        if not candidates[kind]:
            reason = "no visible qualifying geometry in this photo; not evidence the feature is absent from the product"
            if kind == "lens_mark":
                reason += "; texture-only markings are covered by native lens/rim pairs, not separately segmented"
            details.append({"kind": kind, "status": "unavailable", "reason": reason, "images": []})
            continue
        recs = []
        for c, (box, method) in enumerate(candidates[kind]):
            for tile, crop in enumerate(_tiles(box, min(DETAIL_SIDE, (max_side - 8) // 2, max_side - 42))):
                path = directory / f"{kind}-{c:02d}-{tile:02d}.png"
                light = " / SIDE LIGHT" if surface_only else ""
                rec = _pair(photo, drawing, crop, path, f"{row['ref']}: {kind}{light} / native pixels", max_side)
                rec.update(view=f"{row['ref']}: {kind}{light} {c + 1}.{tile + 1}", kind="surface_detail" if surface_only else "look_detail", detail_kind=kind,
                           ref=row["ref"], selection_method=method)
                images.append(rec)
                recs.append(rec)
        details.append({"kind": kind, "status": "visible", "images": recs})
    colours = {}
    for key in ("front_face", "front_wall", "inner_rim_wall"):
        # One pixel removes antialias mixture without erasing narrow (but real) walls wholesale.
        region = binary_erosion(selected[key], iterations=1)
        colours[key] = {"photo": _stats(photo, region), "drawing": _stats(drawing, region)}
    scale = float(row["camera"].get("meta", {}).get("pixel_scale", 1.))
    for rec in images:
        rec.update(source_pixel_scale=scale, source_photo_box=[v / scale for v in rec["crop_box"]],
                   source_photo_box_convention="half-open pixel-edge coordinates; no pixel-centre shift")
    return {"images": images, "details": details, "surface_colours": colours, "sheet_path": full["path"],
        "method": "Same camera/crop; no upscaling, colour adjustment or contrast stretch. Native detail means capture pixels. "
        "First-hit geometric normals in +Z-forward asset coordinates: front face |nz|>=0.85, wall |nz|<=0.5; "
        "inner rim wall is within 2 mm of glass, front depth within 12 mm behind glass. Masks eroded one pixel. "
        "These are surface-orientation bands, not a claim that dark pixels are physical walls or lit pixels are front faces. "
        "Same masks on photos can include other regions when registration differs; statistics are information only."}
