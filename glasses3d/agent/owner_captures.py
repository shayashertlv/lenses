"""Validated owner marks on model screenshots, kept separate from product photos.

The public facades do no inference and start neither a worker nor a paid client.
Anchors describe evidence only; authenticated model-map selections grant edit scope.
"""
from __future__ import annotations

import copy
from io import BytesIO
import json
import math
from pathlib import Path
import re
import shutil

import numpy as np
from PIL import Image

from . import edits, delivery, procs
from ..g3d_host import glb

SCHEMA = "g3d.owner_capture/1"
ITEMS_PATH = "inputs/owner_captures/items.json"
SOURCE_PATH = "inputs/owner_captures/s.glb"
POSITION_TOLERANCE_MM = 0.05
PROJECTION_TOLERANCE_PX = 3.0


def _object(value, keys, what):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise ValueError(f"{what} is missing required details or contains unknown details.")
    return value


def _number(value, what, *, minimum=None, maximum=None):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{what} must be a finite number.")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"{what} is outside its allowed range.")
    return float(value)


def _integer(value, what, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{what} must be a whole number within its allowed range.")
    return value


def _vector(value, length, what):
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{what} needs {length} numbers.")
    return np.array([_number(x, what) for x in value], dtype=float)


def _text(value, what, *, blank=False):
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise ValueError(f"{what} must contain text.")
    return value


def _bytes(value):
    return value if isinstance(value, bytes) else Path(value).read_bytes()


def _png(value, size, what):
    try:
        data = _bytes(value)
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or image.size != size or getattr(image, "n_frames", 1) != 1:
                raise ValueError
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        raise ValueError(f"The {what} must be a readable single-frame PNG at the capture's exact size.") from None
    return data


def _camera(camera, width, height):
    _object(camera, "projection fov_y_deg aspect near_m far_m position_m quaternion_xyzw target_m matrix_world projection_matrix",
            "The capture camera")
    if camera["projection"] != "perspective":
        raise ValueError("The capture camera must use perspective projection.")
    fov = _number(camera["fov_y_deg"], "The camera field of view", minimum=0.001, maximum=179.999)
    aspect = _number(camera["aspect"], "The camera aspect", minimum=1e-12)
    near = _number(camera["near_m"], "The camera near distance", minimum=1e-12)
    far = _number(camera["far_m"], "The camera far distance", minimum=near)
    if far <= near or not math.isclose(aspect, width / height, rel_tol=1e-4):
        raise ValueError("The camera aspect or depth range does not match the capture.")
    position = _vector(camera["position_m"], 3, "The camera position")
    target = _vector(camera["target_m"], 3, "The camera target")
    quaternion = _vector(camera["quaternion_xyzw"], 4, "The camera rotation")
    if not math.isclose(float(np.linalg.norm(quaternion)), 1, abs_tol=1e-5):
        raise ValueError("The camera rotation must have unit length.")
    world = _vector(camera["matrix_world"], 16, "The camera world matrix").reshape(4, 4).T
    expected_world = glb.node_local_matrix({"rotation": quaternion, "translation": position})
    if not np.allclose(world, expected_world, rtol=1e-5, atol=1e-7):
        raise ValueError("The camera matrix does not match its position and rotation.")
    direction = target - position
    distance = float(np.linalg.norm(direction))
    if (not np.isfinite(direction).all() or not math.isfinite(distance) or distance < 1e-10
            or np.dot(direction / distance, -world[:3, 2]) < 1 - 1e-5):
        raise ValueError("The camera target does not match the direction it faces.")
    projection = _vector(camera["projection_matrix"], 16, "The projection matrix").reshape(4, 4).T
    f = 1 / math.tan(math.radians(fov) / 2)
    expected = np.array([[f / aspect, 0, 0, 0], [0, f, 0, 0],
                         [0, 0, -(far + near) / (far - near), -2 * far * near / (far - near)],
                         [0, 0, -1, 0]])
    if not np.allclose(projection, expected, rtol=1e-5, atol=1e-7):
        raise ValueError("The projection matrix does not match the camera settings.")
    view_projection = projection @ np.linalg.inv(world)
    if not np.isfinite(view_projection).all():
        raise ValueError("The camera settings cannot produce a finite picture projection.")
    return view_projection


def _primitive_index(model):
    result = {}
    for primitive in model.primitives:
        key = (primitive.node_index, primitive.mesh_index, primitive.primitive_index)
        if key in result:
            raise ValueError("The model has ambiguous surface identities; capture it again after exporting.")
        result[key] = primitive
    return result


def _ray_surfaces(model):
    """Prepare actual visible-scene triangles once for nearest-hit verification."""
    triangles, identities, two_sided, lens, winding = [], [], [], [], []
    for primitive in model.primitives:
        if (primitive.error or primitive.compressed or primitive.index_issue or primitive.mode != 4
                or not np.isfinite(primitive.positions).all()):
            raise ValueError("This model has surfaces that cannot be checked for capture anchors.")
        material = model.material(primitive.material_index) or {}
        for index in range(len(primitive.triangles)):
            identities.append((primitive.node_index, primitive.mesh_index, primitive.primitive_index, index))
        triangles.append(primitive.positions[primitive.triangles])
        two_sided.extend([bool(material.get("doubleSided", False))] * len(primitive.triangles))
        lens.extend([_is_lens(model, primitive)] * len(primitive.triangles))
        winding.extend([1 if np.linalg.det(primitive.world[:3, :3]) >= 0 else -1] * len(primitive.triangles))
    if not triangles:
        raise ValueError("The capture model has no readable surfaces.")
    vertices = np.concatenate(triangles)
    return (vertices[:, 0], vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0],
            identities, np.array(two_sided), np.array(lens), np.array(winding))


def _ray_hits(surfaces, origin, direction):
    start, edge1, edge2, identities, two_sided, lens, winding = surfaces
    cross = np.cross(direction, edge2)
    determinant = np.einsum("ij,ij->i", edge1, cross)
    valid = np.where(two_sided, np.abs(determinant) > 1e-15, determinant * winding > 1e-15)
    inverse = np.divide(1., determinant, out=np.zeros_like(determinant), where=valid)
    delta = origin - start
    u = np.einsum("ij,ij->i", delta, cross) * inverse
    q = np.cross(delta, edge1)
    v = q @ direction * inverse
    distances = np.einsum("ij,ij->i", edge2, q) * inverse
    indices = np.flatnonzero(valid & (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7) & (distances > 1e-10))
    return [(float(distances[i]), identities[i], bool(lens[i])) for i in indices[np.argsort(distances[indices])]]


def _visible_anchor(anchor, point, xy, camera, view_projection, size, surfaces):
    origin = np.asarray(camera["position_m"], dtype=float)
    if point is None:
        unprojected = np.linalg.inv(view_projection) @ np.array([xy[0] / size[0] * 2 - 1, 1 - xy[1] / size[1] * 2, 0, 1])
        direction = unprojected[:3] / unprojected[3] - origin
    else:
        direction = point - origin
    direction /= np.linalg.norm(direction)
    hits = _ray_hits(surfaces, origin, direction)
    if point is None:
        if hits:
            raise ValueError("A background mark points at the model; capture its surface again.")
        return
    identity = tuple(anchor[key] for key in ("node_index", "mesh_index", "primitive_index", "triangle_index"))
    distance = float(np.linalg.norm(point - origin))
    tolerance = POSITION_TOLERANCE_MM / 1000
    if not hits or not any(key == identity and abs(depth - hits[0][0]) <= tolerance for depth, key, _ in hits):
        raise ValueError("The marked triangle is hidden behind another surface in this view.")
    if anchor["is_lens"]:
        behind = anchor["behind"]
        rear_hits = [hit for hit in hits if not hit[2] and hit[0] >= distance - tolerance]
        if (behind is None) != (not rear_hits):
            raise ValueError("The lens mark must retain the first non-lens surface behind it.")
        if behind is not None:
            rear_identity = tuple(behind[key] for key in ("node_index", "mesh_index", "primitive_index", "triangle_index"))
            if not any(key == rear_identity and abs(depth - rear_hits[0][0]) <= tolerance for depth, key, _ in rear_hits):
                raise ValueError("The surface behind a lens is not its first non-lens hit.")


def _primitive(value, primitives):
    key = tuple(_integer(value.get(k), "The marked surface index")
                for k in ("node_index", "mesh_index", "primitive_index"))
    primitive = primitives.get(key)
    if (primitive is None or primitive.error or primitive.compressed or primitive.index_issue
            or primitive.mode != 4 or not len(primitive.triangles)
            or not np.isfinite(primitive.positions).all()):
        raise ValueError("The marked surface cannot be found in this model.")
    return primitive


def _is_lens(model, primitive):
    material = model.material(primitive.material_index) or {}
    return (material.get("extensions", {}).get("KHR_materials_transmission", {}).get("transmissionFactor", 0) > 0
            or material.get("pbrMetallicRoughness", {}).get("baseColorFactor", [1, 1, 1, 1])[3] < 1)


def _anchor(anchor, xy, model, primitives, view_projection, size, *, behind=False):
    if isinstance(anchor, dict) and anchor.get("kind") == "background":
        _object(anchor, "kind", "The background mark")
        if behind:
            raise ValueError("A surface behind a lens must identify a model surface.")
        return None
    _object(anchor, "kind node_index node_name mesh_index primitive_index triangle_index barycentric position_mm normal is_lens behind",
            "The marked surface")
    if anchor["kind"] != "surface":
        raise ValueError("Each mark must identify a surface or the background.")
    primitive = _primitive(anchor, primitives)
    if anchor["node_name"] != model.nodes[primitive.node_index].get("name", ""):
        raise ValueError("The marked object name does not match this model.")
    triangle_index = _integer(anchor["triangle_index"], "The marked triangle", maximum=len(primitive.triangles) - 1)
    weights = _vector(anchor["barycentric"], 3, "The surface triangle weights")
    if np.min(weights) < -1e-6 or np.max(weights) > 1 + 1e-6 or abs(float(weights.sum()) - 1) > 1e-5:
        raise ValueError("The surface triangle weights must be between zero and one and add up to one.")
    triangle = primitive.positions[primitive.triangles[triangle_index]]
    if np.linalg.norm(np.cross(triangle[1] - triangle[0], triangle[2] - triangle[0])) <= 1e-16:
        raise ValueError("The marked triangle has no surface area.")
    point = weights @ triangle
    claimed = _vector(anchor["position_mm"], 3, "The marked position")
    if np.linalg.norm(claimed - point * 1000) > POSITION_TOLERANCE_MM:
        raise ValueError("The marked position does not match its model triangle.")
    normal = _vector(anchor["normal"], 3, "The marked surface normal")
    if not math.isclose(float(np.linalg.norm(normal)), 1, abs_tol=1e-3):
        raise ValueError("The marked surface normal must have unit length.")
    lens = _is_lens(model, primitive)
    if type(anchor["is_lens"]) is not bool or anchor["is_lens"] != lens:
        raise ValueError("The marked lens flag does not match the model material.")
    clip = view_projection @ np.r_[point, 1]
    if clip[3] <= 0 or abs(clip[2] / clip[3]) > 1 + 1e-6:
        raise ValueError("The marked surface is outside the captured camera view.")
    projected = np.array([(clip[0] / clip[3] + 1) * size[0] / 2,
                          (1 - clip[1] / clip[3]) * size[1] / 2])
    if np.linalg.norm(projected - xy) > PROJECTION_TOLERANCE_PX:
        raise ValueError("The marked surface does not line up with its point in the picture.")
    if anchor["behind"] is not None:
        if behind or not lens:
            raise ValueError("Only a lens can have one surface behind it.")
        rear = _anchor(anchor["behind"], xy, model, primitives, view_projection, size, behind=True)
        if anchor["behind"]["is_lens"]:
            raise ValueError("The surface behind a lens must be the first non-lens surface.")
        if (view_projection @ np.r_[rear, 1])[3] <= clip[3]:
            raise ValueError("The surface behind the lens must be farther from the camera.")
    return point


def validate_capture(payload, viewer_png, annotated_png, source_glb) -> dict:
    """Validate the frozen browser contract against real GLB triangles and camera math."""
    _object(payload, "schema capture_id source image camera marks request_text", "The capture")
    if payload["schema"] != SCHEMA or not isinstance(payload["capture_id"], str) or not re.fullmatch(r"C[1-9][0-9]{0,8}", payload["capture_id"]):
        raise ValueError("The capture format or capture identifier is invalid.")
    _object(payload["source"], "delivery_key glb_sha256", "The capture source")
    _text(payload["source"]["delivery_key"], "The delivery identifier")
    try:
        digest = edits.sha(source_glb)
        model = glb.load(source_glb)
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        raise ValueError("The source model cannot be read; reopen its delivery and capture again.") from None
    if payload["source"]["glb_sha256"] != digest:
        raise ValueError("This capture belongs to a different model; capture the selected delivery again.")
    _object(payload["image"], "width_px height_px", "The capture image size")
    width = _integer(payload["image"]["width_px"], "The picture width", 1)
    height = _integer(payload["image"]["height_px"], "The picture height", 1)
    if width * height > 40_000_000:
        raise ValueError("The capture is too large to read at its original pixels.")
    size = (width, height)
    _png(viewer_png, size, "clean picture")
    _png(annotated_png, size, "annotated picture")
    view_projection = _camera(payload["camera"], width, height)
    _text(payload["request_text"], "The owner's request", blank=True)
    if not isinstance(payload["marks"], list) or not payload["marks"]:
        raise ValueError("Add at least one labelled mark to the capture.")
    labels, primitives, surfaces = set(), _primitive_index(model), _ray_surfaces(model)
    for mark in payload["marks"]:
        _object(mark, "label kind radius_px stroke_px vertices footprint", "Each mark")
        label = _text(mark["label"], "Each mark's label")
        if label != label.strip() or label.casefold() in labels:
            raise ValueError("Every mark needs a unique label without leading or trailing spaces.")
        labels.add(label.casefold())
        if mark["kind"] not in ("point", "polyline", "closed_shape"):
            raise ValueError("Choose a point, a line, or a closed shape for each mark.")
        for key in ("radius_px", "stroke_px"):
            _number(mark[key], "The mark size", minimum=0, maximum=max(size))
        if (mark["kind"] == "point" and mark["radius_px"] <= 0) or (mark["kind"] != "point" and mark["stroke_px"] <= 0):
            raise ValueError("The dot or line must have a visible size.")
        vertices = mark["vertices"]
        minimum = {"point": 1, "polyline": 2, "closed_shape": 3}[mark["kind"]]
        if not isinstance(vertices, list) or len(vertices) < minimum or (mark["kind"] == "point" and len(vertices) != 1):
            raise ValueError("The mark has the wrong number of points for its shape.")
        distinct = set()
        for vertex in vertices:
            _object(vertex, "xy_px anchor", "Each marked point")
            xy = _vector(vertex["xy_px"], 2, "The picture point")
            if not (0 <= xy[0] < width and 0 <= xy[1] < height):
                raise ValueError("A marked point is outside the picture.")
            distinct.add(tuple(xy))
            point = _anchor(vertex["anchor"], xy, model, primitives, view_projection, size)
            _visible_anchor(vertex["anchor"], point, xy, payload["camera"], view_projection, size, surfaces)
        if mark["kind"] == "closed_shape" and len(distinct) != len(vertices):
            raise ValueError("A closed shape needs distinct corners; do not repeat its closing point.")
        footprint = _object(mark["footprint"], "pixels faces", "The marked footprint")
        pixels = _integer(footprint["pixels"], "The footprint pixel count", maximum=width * height)
        if not isinstance(footprint["faces"], list) or (not pixels and footprint["faces"]):
            raise ValueError("The footprint's surfaces and pixel count do not agree.")
        seen = set()
        for face in footprint["faces"]:
            _object(face, "node_index mesh_index primitive_index triangles", "A footprint surface")
            primitive = _primitive(face, primitives)
            key = (primitive.node_index, primitive.mesh_index, primitive.primitive_index)
            if key in seen or not isinstance(face["triangles"], list) or not face["triangles"]:
                raise ValueError("Each footprint surface needs one nonempty list of triangle ranges.")
            seen.add(key)
            last = -1
            for pair in face["triangles"]:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("Footprint triangle ranges need a first and last triangle.")
                first = _integer(pair[0], "The first footprint triangle", maximum=len(primitive.triangles) - 1)
                end = _integer(pair[1], "The last footprint triangle", first, len(primitive.triangles) - 1)
                if first <= last:
                    raise ValueError("Footprint triangle ranges must be ordered without overlaps.")
                last = end
    return copy.deepcopy(payload)


def load_round_captures(root, owner_edit=None) -> list[dict]:
    """Read hash-verified capture bundles; returned Paths are confined to this round."""
    root = Path(root).resolve()
    if owner_edit is None:
        if (root / "inputs/owner-edit.json").is_file():
            owner_edit = edits.read(root / "inputs/owner-edit.json")
        elif (root / "inputs/full-edit-item.json").is_file():
            from . import owner_fallback
            owner_edit = owner_fallback.capture_context(owner_fallback.load(root))
        else:
            return []
    doc = owner_edit
    rows = doc.get("owner_capture_items")
    if rows is None:
        return []
    if not isinstance(rows, list) or not rows:
        raise ValueError("The owner capture items are missing.")
    def verified(name):
        path = edits.confined(root, name)
        if not path.is_file() or edits.sha(path) != doc.get("immutable_files", {}).get(name):
            raise ValueError("The owner capture inputs changed; prepare a new edit request.")
        return path
    if edits.read(verified(ITEMS_PATH)) != rows:
        raise ValueError("The owner capture items changed; prepare a new edit request.")
    source = verified(SOURCE_PATH)
    if edits.sha(source) != doc["source"]["export_sha256"]:
        raise ValueError("The capture source differs from the selected delivery.")
    result, labels, ids, captures = [], set(), set(), set()
    for row in rows:
        _object(row, "item_id capture_id labels request_text capture_path", "Each owner item")
        if (not isinstance(row["item_id"], str) or not re.fullmatch(r"I[1-9][0-9]{0,8}", row["item_id"])
                or not isinstance(row["capture_id"], str) or not re.fullmatch(r"C[1-9][0-9]{0,8}", row["capture_id"])
                or row["item_id"] in ids or row["capture_id"] in captures):
            raise ValueError("Every owner item and capture needs a distinct identifier.")
        ids.add(row["item_id"])
        captures.add(row["capture_id"])
        expected = f"inputs/owner_captures/{row['capture_id']}/capture.json"
        if row["capture_path"] != expected:
            raise ValueError("The capture must stay in its own input folder.")
        metadata = verified(expected)
        folder = metadata.parent
        viewer = verified((folder / "viewer.png").relative_to(root).as_posix())
        annotated = verified((folder / "annotated.png").relative_to(root).as_posix())
        payload = validate_capture(edits.read(metadata), viewer, annotated, source)
        actual_labels = [mark["label"] for mark in payload["marks"]]
        if (payload["capture_id"] != row["capture_id"] or actual_labels != row["labels"]
                or payload["request_text"] != row["request_text"]):
            raise ValueError("The capture labels or owner's words differ from the saved item.")
        if any(label.casefold() in labels for label in actual_labels):
            raise ValueError("Each label must be unique across all items in this edit session.")
        labels.update(label.casefold() for label in actual_labels)
        result.append({**copy.deepcopy(row), "payload": payload, "metadata_sha256": edits.sha(metadata),
                       "capture_dir": folder, "viewer_path": viewer, "viewer_sha256": edits.sha(viewer),
                       "annotated_path": annotated, "annotated_sha256": edits.sha(annotated), "source_glb": source})
    if doc["request_text"] != _request_text(rows):
        raise ValueError("The owner's item words differ from the saved request.")
    return result


def _request_text(items):
    return "\n\n".join(f"Item {item['item_id']} ({item['capture_id']}):\n{item['request_text']}" for item in items)


def create_capture_understand(source_root, delivery_id, items, destination=None) -> Path:
    """Create an unresolved UNDERSTAND from the chosen immutable editable delivery."""
    source_root = Path(source_root).resolve()
    saved = delivery.load_delivery(source_root, delivery_id)
    source_glb = Path(saved["model"])
    if not isinstance(items, list) or not items:
        raise ValueError("Add at least one edit item before sending it to Astra.")
    records, bundles, item_ids, capture_ids, labels = [], [], set(), set(), set()
    for item in items:
        _object(item, "item_id request_text capture_dir", "Each edit item")
        item_id = item["item_id"]
        if not isinstance(item_id, str) or not re.fullmatch(r"I[1-9][0-9]{0,8}", item_id) or item_id in item_ids:
            raise ValueError("Every edit item needs its own item identifier.")
        item_ids.add(item_id)
        _text(item["request_text"], "The owner's request", blank=True)
        folder = Path(item["capture_dir"]).resolve()
        metadata = edits.confined(folder, "capture.json")
        viewer = edits.confined(folder, "viewer.png")
        annotated = edits.confined(folder, "annotated.png")
        payload = validate_capture(edits.read(metadata), viewer, annotated, source_glb)
        capture_id = payload["capture_id"]
        if capture_id in capture_ids:
            raise ValueError("Each item must use a different capture.")
        capture_ids.add(capture_id)
        names = [mark["label"] for mark in payload["marks"]]
        if any(label.casefold() in labels for label in names):
            raise ValueError("Each label must be unique across all items in this edit session.")
        labels.update(label.casefold() for label in names)
        # The draft's text box may have changed since the screenshot was captured.
        # Copy its final verbatim words into the new immutable metadata, never the draft.
        payload["request_text"] = item["request_text"]
        records.append({"item_id": item_id, "capture_id": capture_id, "labels": names,
                        "request_text": item["request_text"],
                        "capture_path": f"inputs/owner_captures/{capture_id}/capture.json"})
        bundles.append((payload, _bytes(viewer), _bytes(annotated)))
    root = edits.create_understand(source_root, _request_text(records), delivery_id=saved["delivery_id"],
                                   destination=destination)
    doc = edits.read(root / "inputs/owner-edit.json")
    # An edit of an edited delivery starts a new capture session. Do not retain
    # earlier items, coverage declarations, or source-model captures as new inputs.
    inherited = root / "inputs/owner_captures"
    if inherited.exists():
        if inherited.resolve() != root.resolve() / "inputs/owner_captures":
            raise ValueError("The capture folder must stay inside this edit round.")
        shutil.rmtree(inherited)
    inherited.mkdir()
    coverage = root / "inputs/edit-capabilities.json"
    if coverage.exists():
        coverage.unlink()
    shutil.copyfile(source_glb, root / SOURCE_PATH)
    for row, (payload, viewer_data, annotated_data) in zip(records, bundles):
        path = root / row["capture_path"]
        path.parent.mkdir()
        (path.parent / "viewer.png").write_bytes(viewer_data)
        (path.parent / "annotated.png").write_bytes(annotated_data)
        procs.write_json_atomic(path, payload)
    procs.write_json_atomic(root / ITEMS_PATH, records)
    doc["owner_capture_items"] = records
    doc["immutable_files"].update({"inputs/owner_captures/" + name: digest
                                     for name, digest in edits.tree_hashes(inherited).items()})
    load_round_captures(root, doc)
    edits._refresh_brief(root, doc)
    procs.write_json_atomic(root / "inputs/owner-edit.json", doc)
    return root
