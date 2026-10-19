"""Immutable, content-bound job deliveries; mutable compatibility copies are never verdict identities."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import time
from uuid import uuid4

from . import procs
from .money import owner_money
from .run_statuses import STATUSES

SCHEMA = "g3d.delivery/1"
POINTER_SCHEMA = "g3d.delivery_current/1"
REPORT_SCHEMA = "g3d.deliverable/1"
DRAFT_STATUSES = frozenset(STATUSES) - {"completed"}
_REVERIFY_STATUSES = frozenset({"failed", "failed_worker"})
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,23}-[0-9a-f]{64}")


class DeliveryError(ValueError):
    """A finished run or immutable delivery is incomplete, stale or inconsistent."""


class DraftSourceError(DeliveryError):
    """Keep each failed source verification when a failed run cannot yield a draft."""

    def __init__(self, message, attempts):
        super().__init__(message)
        self.checkpoint_attempts = copy.deepcopy(attempts)


def _json_bytes(value) -> bytes:
    try:
        return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DeliveryError(f"delivery contains invalid JSON data: {exc}") from exc


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(root: Path, path) -> Path:
    if not isinstance(path, (str, Path)) or not str(path):
        raise DeliveryError("delivery file path is missing")
    target = (root / path).resolve()
    if target == root or not target.is_relative_to(root):
        raise DeliveryError("delivery file path escapes its directory")
    return target


def _output_path(root: Path, path) -> Path:
    """A mutable output may never alias an immutable snapshot, even through an in-job link."""
    target = _inside(root, path)
    if target != (root / path).absolute():
        raise DeliveryError("delivery output path contains a filesystem link")
    return target


def _relative(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute()
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise DeliveryError("delivery manifest needs confined relative file paths")
    return value


def _read(root: Path, path, expected: str | None = None) -> tuple[Path, bytes]:
    target = _inside(root, path)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise DeliveryError(f"delivery file is missing or unreadable: {target.name}") from exc
    if expected is not None and (not isinstance(expected, str) or not _DIGEST.fullmatch(expected)
                                 or _sha(data) != expected):
        raise DeliveryError(f"delivery file hash changed: {target.name}")
    return target, data


def _document(data: bytes, what: str) -> dict:
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise DeliveryError(f"{what} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise DeliveryError(f"{what} must be an object")
    return value


def read_checkpoint_snapshot(root, checkpoint, fingerprint, *, blender=None, selftest=False):
    """Measure an immutable checkpoint on a private copy, without moving the live journal/head."""
    from .journal import CheckpointIndex
    from .tool_timeouts import OWNER_SNAPSHOT_DEADLINE_S
    from .worker_client import WorkerClient

    root = Path(root).resolve()
    saved = CheckpointIndex(root / "checkpoints").get(checkpoint)
    if not saved or saved.get("fingerprint") != fingerprint:
        raise DeliveryError("side-effect measurement checkpoint does not match the delivered fingerprint")
    source, data = _read(root / "checkpoints", _relative(saved.get("file")), saved.get("sha256", ""))
    work = _output_path(root, Path("work/model_maps") / ("fallback-effects-" + uuid4().hex))
    copied, snapshot_path = work / "source.blend", work / "snapshot.json"
    _write_file(copied, data)
    worker = WorkerClient(root, start_blend=copied, worker_dir=work / "worker", blender=blender, selftest=selftest)
    try:
        worker.start()
        if worker.hello.get("fingerprint") != fingerprint:
            raise DeliveryError("side-effect measurement did not reopen the delivered checkpoint exactly")
        measured = worker.call("model_map_snapshot", {"path": str(snapshot_path)},
                               deadline_s=OWNER_SNAPSHOT_DEADLINE_S)
        if (measured.get("status") != "ok" or measured.get("unchanged") is not True
                or measured.get("fingerprint_before") != fingerprint
                or measured.get("fingerprint_after") != fingerprint):
            raise DeliveryError("side-effect measurement changed or could not read the delivered checkpoint")
        _, payload = _read(root, snapshot_path, measured.get("sha256", ""))
        snapshot = _document(payload, "side-effect mesh snapshot")
        if snapshot.get("fingerprint") != fingerprint:
            raise DeliveryError("side-effect snapshot belongs to another checkpoint")
        _read(root, source, saved["sha256"])
        return snapshot
    finally:
        worker.close()


def measure_full_edit_effects(before, after, marks, *, item_id, source_glb_sha256, delivered_glb_sha256):
    """Describe observable changes, never authorize an edit or turn a measurement into a quality gate.

    Object IDs are snapshot-local ordinals: correspondence is by unique exact object name. Source polygons
    come only from C1's authenticated capture correspondence. A boundary vertex also changes its unmarked
    incident polygons. Remeshing, ambiguous names and incomplete marks retain explicit uncertainty.
    """
    import numpy as np
    from collections import Counter, defaultdict

    selected = [copy.deepcopy(mark) for mark in marks if mark.get("item_id", item_id) == item_id]
    complete_marks = bool(selected) and all(mark.get("status") == "resolved" for mark in selected)
    regions = defaultdict(set)
    source_ids = {obj["id"]: obj["name"] for obj in before["objects"]}
    for mark in selected:
        for region in mark.get("resolved_polygons", []) + mark.get("behind_polygons", []):
            name = source_ids.get(region.get("object_id"))
            if name != region.get("object_name"):
                complete_marks = False
                continue
            regions[name].update(region["polygon_ids"])

    fields = {
        "geometry": ("vertices_local", "vertices_world_mm", "edges", "loops", "polygons", "triangles",
                     "triangle_polygons", "polygon_smooth", "corner_normals", "normal_attributes",
                     "normal_intent", "has_custom_normals", "evaluated"),
        "materials": ("material_names", "material_definitions", "material_sha256", "material_slot_links",
                      "polygon_material"),
        "uvs": ("uv_layers", "uv_active_index"),
        "transforms": ("matrix_world", "parent_name"),
    }

    def digest_fields(obj, keys):
        return _sha(_json_bytes({key: obj[key] for key in keys if key in obj})) if obj is not None else None

    def objects(snapshot):
        out = defaultdict(list)
        for obj in snapshot["objects"]:
            out[obj["name"]].append(obj)
        return out

    def polygon_signatures(obj):
        vertices = obj["vertices_world_mm"]
        # Exact unordered corners prove unchanged polygon geometry; no spatial-nearest guess after remeshing.
        return [tuple(sorted(tuple(vertices[i]) for i in polygon)) for polygon in obj["polygons"]]

    def changed_geometry(a, b, marked):
        if a is None:
            return {"status": "partially_resolved", "correspondence": "new_object",
                    "added_polygon_count": len(b["polygons"]), "outside_resolved_region": None,
                    "outside_item_region": None,
                    "reason": "Added or renamed object has no proven correspondence to the marked source polygons."}
        if b is None:
            count = len(a["polygons"])
            changed = list(range(count))
            outside = [i for i in changed if i not in marked]
            return {"status": "measured", "correspondence": "removed_object",
                    "changed_polygon_ids": changed, "outside_resolved_polygon_ids": outside,
                    "outside_resolved_region": bool(outside),
                    "outside_item_region": bool(outside) if complete_marks else None}
        va, vb = np.asarray(a["vertices_world_mm"], float), np.asarray(b["vertices_world_mm"], float)
        same = a["polygons"] == b["polygons"] and va.shape == vb.shape
        if not same:
            remaining = Counter(polygon_signatures(b))
            removed = []
            for pid, signature in enumerate(polygon_signatures(a)):
                if remaining[signature]:
                    remaining[signature] -= 1
                else:
                    removed.append(pid)
            outside = sorted(set(removed) - marked)
            return {"status": "partially_resolved", "correspondence": "topology_changed",
                    "changed_source_polygon_ids": removed, "added_or_changed_polygon_count": sum(remaining.values()),
                    "outside_resolved_polygon_ids": outside, "outside_resolved_region": bool(outside) or None,
                    "outside_item_region": True if outside and complete_marks else None,
                    "reason": "Changed topology has no proven vertex correspondence. Exact surviving polygon geometry is compared; new geometry remains unlocalized."}
        changed_vertices = set(np.flatnonzero(np.any(va != vb, axis=1)).tolist())
        if a.get("vertices_local") != b.get("vertices_local"):
            la, lb = np.asarray(a["vertices_local"]), np.asarray(b["vertices_local"])
            changed_vertices.update(np.flatnonzero(np.any(la != lb, axis=1)).tolist())
        unresolved = []
        ea, eb = a.get("evaluated", {}), b.get("evaluated", {})
        if ea != eb:
            eva, evb = np.asarray(ea.get("vertices_world_mm", [])), np.asarray(eb.get("vertices_world_mm", []))
            if (ea.get("topology_matches_source") and eb.get("topology_matches_source")
                    and eva.shape == evb.shape == va.shape):
                changed_vertices.update(np.flatnonzero(np.any(eva != evb, axis=1)).tolist())
            else:
                unresolved.append("Changed evaluated geometry has no proven correspondence to source polygons.")
        changed_polygons = {pid for pid, polygon in enumerate(a["polygons"])
                            if changed_vertices.intersection(polygon)}
        for left, right, key in ((a, b, "corner_normals"), (a, b, "polygon_smooth"), (ea, eb, "corner_normals")):
            if left.get(key) == right.get(key):
                continue
            x, y = left.get(key, []), right.get(key, [])
            expected = len(a["polygons"]) if key == "polygon_smooth" else sum(map(len, a["polygons"]))
            if len(x) != expected or len(y) != expected:
                unresolved.append("Changed shading data has no proven source polygon correspondence.")
                continue
            cursor = 0
            for pid, polygon in enumerate(a["polygons"]):
                width = 1 if key == "polygon_smooth" else len(polygon)
                if x[cursor:cursor+width] != y[cursor:cursor+width]:
                    changed_polygons.add(pid)
                cursor += width
        if a.get("triangles") != b.get("triangles"):
            unresolved.append("Changed triangulation is reported separately from source polygon positions.")
        if a.get("edges") != b.get("edges") or a.get("loops") != b.get("loops"):
            unresolved.append("Changed raw edge/loop data has no proven source polygon correspondence.")
        outside = sorted(changed_polygons - marked)
        allowed_vertices = {i for pid in marked if 0 <= pid < len(a["polygons"]) for i in a["polygons"][pid]}
        displacement = np.linalg.norm(va - vb, axis=1)
        return {"status": "partially_resolved" if unresolved else "measured",
                "correspondence": "same_vertex_count_and_polygon_connectivity",
                "changed_vertex_ids": sorted(changed_vertices), "changed_vertex_count": len(changed_vertices),
                "max_vertex_displacement_mm": float(displacement.max()) if len(displacement) else 0.0,
                "changed_polygon_ids": sorted(changed_polygons), "outside_resolved_polygon_ids": outside,
                "outside_resolved_vertex_ids": sorted(changed_vertices - allowed_vertices),
                "outside_resolved_region": bool(outside) if outside or not unresolved else None,
                "outside_item_region": (bool(outside) if complete_marks and (outside or not unresolved) else None),
                "unresolved": unresolved}

    old, new = objects(before), objects(after)
    rows = []
    for name in sorted(old.keys() | new.keys()):
        aa, bb = old.get(name, []), new.get(name, [])
        if len(aa) > 1 or len(bb) > 1:
            rows.append({"name": name, "status": "unresolved", "source_count": len(aa), "delivered_count": len(bb),
                         "reason": "The exact object name is not unique; no object correspondence was invented."})
            continue
        a, b = (aa[0] if aa else None), (bb[0] if bb else None)
        row = {"name": name, "source_object_id": a["id"] if a else None,
               "delivered_object_id": b["id"] if b else None,
               "status": "added" if a is None else "removed" if b is None else "compared"}
        for category, keys in fields.items():
            previous, current = digest_fields(a, keys), digest_fields(b, keys)
            row[category] = {"changed": previous != current, "source_sha256": previous, "delivered_sha256": current,
                             "changed_fields": [key for key in keys if (a or {}).get(key) != (b or {}).get(key)]}
        row["changed"] = any(row[key]["changed"] for key in fields)
        row["marked_source_polygon_ids"] = sorted(regions[name])
        if row["geometry"]["changed"]:
            row["geometry"].update(changed_geometry(a, b, regions[name]))
        else:
            row["geometry"].update(status="unchanged", outside_resolved_region=False, outside_item_region=False)
        rows.append(row)
    unsupported_changed = before.get("unsupported_rendered_geometry", []) != after.get("unsupported_rendered_geometry", [])
    resources = {}
    for kind in ("images", "node_groups"):
        left, right = before.get("scene_state", {}).get(kind, {}), after.get("scene_state", {}).get(kind, {})
        resources[kind] = [name for name in sorted(left.keys() | right.keys()) if left.get(name) != right.get(name)]
    for row in rows:
        if "materials" in row and any(resources.values()):
            row["materials"].update(shared_resource_changes=copy.deepcopy(resources),
                dependency_resolution="unresolved",
                note="Shared image/node-group content changed. Snapshot material hashes alone cannot attribute those resource changes to this object.")
    outside = [row["name"] for row in rows if row.get("geometry", {}).get("outside_item_region") is True]
    unresolved = [row["name"] for row in rows if row.get("status") == "unresolved"
                  or row.get("geometry", {}).get("status") == "partially_resolved"
                  or row.get("geometry", {}).get("outside_item_region") is None]
    return {"schema": "g3d.full_edit_side_effects/1", "informational_only": True, "item_id": item_id,
            "status": "partially_resolved" if unresolved or not complete_marks or unsupported_changed or any(resources.values()) else "measured",
            "source_glb_sha256": source_glb_sha256, "delivered_glb_sha256": delivered_glb_sha256,
            "source_fingerprint": before["fingerprint"], "delivered_fingerprint": after["fingerprint"],
            "object_correspondence": "unique exact object names; snapshot-local ordinal IDs are not persistent",
            "region_resolution": "resolved" if complete_marks else "unresolved_or_partial",
            "region_basis": "Union of C1-resolved footprint and anchor source polygons, including behind-lens anchors. Polygon granularity cannot prove confinement inside a smaller pixel mark.",
            "owner_mark_resolutions": selected, "objects": rows,
            "summary": {"changed_objects": [row["name"] for row in rows if row.get("changed")],
                        "geometry_outside_item_region": True if outside else None if unresolved or not complete_marks or unsupported_changed else False,
                        "outside_region_objects": outside, "unresolved_objects": unresolved},
            "origin_changed": before.get("origin_mm") != after.get("origin_mm"),
            "shared_material_resource_changes": resources,
            "unsupported_rendered_geometry_changed": unsupported_changed,
            "unsupported_rendered_geometry": {"source": before.get("unsupported_rendered_geometry", []),
                                               "delivered": after.get("unsupported_rendered_geometry", [])}}


def _full_edit_effects(root, result, exp, checkpoint, fingerprint, files):
    """Seal diagnostics with ordinary delivery checks; measurement trouble never hides a usable model."""
    if not (root / "inputs/full-edit-item.json").exists() and result.get("mode") != "full_edit_item":
        return None
    from . import owner_fallback

    context = owner_fallback.load(root)
    if context is None:
        raise DeliveryError("the full item edit's immutable input binding is missing")
    if result.get("item_id", context["item_id"]) != context["item_id"]:
        raise DeliveryError("the full item edit's item identity changed after the run")
    baseline_path = context["baseline_snapshot"]
    _, baseline_bytes = _read(root, baseline_path, context["immutable_files"][baseline_path])
    baseline = _document(baseline_bytes, "full item edit source snapshot")
    if baseline.get("fingerprint") != context["source"]["fingerprint"]:
        raise DeliveryError("the full item edit's source snapshot fingerprint changed")
    files["side-effects/source-snapshot.json"] = baseline_bytes
    files["side-effects/marks.json"] = _json_bytes(context["owner_mark_resolutions"])
    try:
        current = read_checkpoint_snapshot(root, checkpoint, fingerprint)
        files["side-effects/delivered-snapshot.json"] = _json_bytes(current)
        report = measure_full_edit_effects(baseline, current, context["owner_mark_resolutions"],
            item_id=context["item_id"], source_glb_sha256=context["source"]["export_sha256"],
            delivered_glb_sha256=exp["sha256"])
    except Exception as exc:
        report = {"schema": "g3d.full_edit_side_effects/1", "informational_only": True,
                  "status": "unavailable", "item_id": context["item_id"],
                  "source_glb_sha256": context["source"]["export_sha256"], "delivered_glb_sha256": exp["sha256"],
                  "source_fingerprint": context["source"]["fingerprint"], "delivered_fingerprint": fingerprint,
                  "objects": [], "summary": {"geometry_outside_item_region": None},
                  "reason": f"Side-effect measurement unavailable: {type(exc).__name__}: {exc}"}
    report["understanding_sha256"] = context["understanding_sha256"]
    report["evidence"] = {name: {"sha256": _sha(payload), "bytes": len(payload)}
                          for name, payload in files.items() if name.startswith("side-effects/")}
    files["side-effects/report.json"] = _json_bytes(report)
    return {"origin": "full_edit_item", "item_id": context["item_id"], "side_effects": report,
            "side_effects_report": {"path": "side-effects/report.json",
                                    "sha256": _sha(files["side-effects/report.json"])}}


def _capture_files(root: Path, ar: dict, glb_sha: str, *, draft=False) -> tuple[dict, list, list, list]:
    """Recheck both the renderer's raw images and the tool images actually shown to Astra."""
    if (not isinstance(ar, dict) or ar.get("export_sha256") != glb_sha
            or (not draft and (ar.get("status") != "ok" or ar.get("exit_code") != 0))):
        raise DeliveryError("delivery requires a successful AR capture of the finished export")
    success = ar.get("status") == "ok" and ar.get("exit_code") == 0
    if draft:
        from .tools.arview import AR_APPEARANCE_POLICY, LENS_ENV_INTENSITY
        if (ar.get("fixed_scale") is not True or ar.get("appearance_policy") != AR_APPEARANCE_POLICY
                or ar.get("lens_env_intensity") != LENS_ENV_INTENSITY):
            raise DeliveryError("draft AR attempt must use the fixed appearance and scale")
        if not ar.get("report"):
            if success or ar.get("images") != [] or ar.get("sheets") != []:
                raise DeliveryError("draft AR capture evidence is incomplete")
            return {}, [], [], []
    report_path, report_bytes = _read(root, ar.get("report"), ar.get("report_sha256", ""))
    capture = _document(report_bytes, "AR report")
    inputs, request = capture.get("input"), capture.get("request")
    if (capture.get("schema") != "arview.capture/1" or not isinstance(inputs, dict) or inputs.get("sha256") != glb_sha
            or capture.get("exit_code") != ar.get("exit_code")
            or not isinstance(request, dict) or request.get("fixed_scale") is not True):
        raise DeliveryError("AR report does not describe a successful fixed-scale capture of this GLB")
    records = capture.get("images")
    if not isinstance(records, list) or (not records and (not draft or success)):
        raise DeliveryError("AR report has no capture images")
    raw, names = [], set()
    for rec in records:
        if not isinstance(rec, dict):
            raise DeliveryError("invalid AR capture image record")
        name = _relative(rec.get("file"))
        if name.casefold() in names or name.casefold() == "report.json":
            raise DeliveryError("AR capture image names collide")
        names.add(name.casefold())
        _, data = _read(report_path.parent, name, rec.get("sha256", ""))
        raw.append((name, data))
    if "worn_views" in ar:
        expected = {rec["file"]: rec for rec in records if rec.get("kind") == "worn_crop"}
        views = ar["worn_views"]
        if (not isinstance(views, list) or len(views) != len(expected)
                or {rec.get("file") for rec in views if isinstance(rec, dict)} != set(expected)):
            raise DeliveryError("worn views differ from the capture report")
        for rec in views:
            original = expected[rec["file"]]
            if (rec.get("sha256") != original["sha256"]
                    or _inside(root, rec.get("path")) != _inside(report_path.parent, original["file"])):
                raise DeliveryError("worn view identity differs from the capture report")
    images = ar.get("images")
    sheets = ar.get("sheets")
    if (not isinstance(images, list) or not isinstance(sheets, list)
            or ((not images or not sheets) and (not draft or success or records))):
        raise DeliveryError("AR contact sheets or their image receipts are missing")
    shown, source_hashes = [], {}
    for rec in images:
        if not isinstance(rec, dict):
            raise DeliveryError("invalid AR tool image record")
        image_path, data = _read(root, rec.get("path"), rec.get("sha256", ""))
        source = rec.get("source_path", rec.get("path"))
        source_sha = rec.get("source_sha256", rec.get("sha256", ""))
        source_path, source_data = _read(root, source, source_sha)
        source_hashes[source_path] = _sha(source_data)
        shown.append((rec, image_path, data, source_path))
    contact, seen = [], set()
    for value in sheets:
        path = _inside(root, value)
        if path in seen:
            raise DeliveryError("AR contact sheet is repeated")
        seen.add(path)
        if path not in source_hashes:
            raise DeliveryError("AR contact sheet has no matching hashed image receipt")
        _, data = _read(root, path, source_hashes[path])
        contact.append((path, data))
    return {"path": report_path, "bytes": report_bytes, "data": capture}, raw, shown, contact


def _snapshot_id(manifest: dict) -> str:
    core = {key: value for key, value in manifest.items() if key != "id"}
    run_id = core.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise DeliveryError("delivery run id is missing")
    label = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id).strip("._-")[:24] or "run"
    return label + "-" + _sha(_json_bytes(core))


def _prepare(root: Path, scene, result: dict) -> tuple[dict, dict[str, bytes]]:
    finished = scene.finish
    if not isinstance(finished, dict) or finished.get("status") != "accepted":
        raise DeliveryError("delivery requires an accepted finish")
    checkpoint = finished.get("checkpoint")
    exit_head = result.get("exit_checkpoint") or {}
    if not checkpoint or exit_head.get("verified") is not True or exit_head.get("id") != checkpoint:
        raise DeliveryError("delivery requires the finished checkpoint to be verified at shutdown")
    exp = finished.get("export") or {}
    if not isinstance(exp, dict):
        raise DeliveryError("finished export record is invalid")
    validation = exp.get("validate_spec") or {}
    seating = exp.get("seat_check")
    printing = exp.get("print_check")
    if (exp.get("status") != "exported" or exp.get("checkpoint") != checkpoint or exp.get("drift")
            or not finished.get("fingerprint") or exp.get("fingerprint") != finished["fingerprint"]
            or not isinstance(validation, dict) or validation.get("ok") is not True
            or validation.get("sha256") != exp.get("sha256") or exp.get("validation_sha256") != exp.get("sha256")
            or not isinstance(seating, dict) or type(seating.get("ok")) is not bool
            or not isinstance(printing, dict) or printing.get("ok") is not True
            or printing.get("glb_sha256") != exp.get("sha256")):
        raise DeliveryError("finished export is invalid or does not match the accepted checkpoint")
    _, model = _read(root, exp.get("path"), exp.get("sha256", ""))
    ar = scene.last_ar
    capture, raw, shown, contact = _capture_files(root, ar, exp["sha256"])
    return _package(root, result, exp, checkpoint, finished["fingerprint"], exit_head,
                    finished.get("summary", ""), ar, model, capture, raw, shown, contact,
                    look=finished.get("look_comparison"), finish_images=finished.get("images", []),
                    owner_edit_report=finished.get("owner_edit_report"))


def _map_paths(value, paths):
    if isinstance(value, dict):
        return {key: _map_paths(item, paths) for key, item in value.items()}
    if isinstance(value, list):
        return [_map_paths(item, paths) for item in value]
    return paths.get(value, value) if isinstance(value, str) else value


def _look_files(root, look, glb_sha, files, *, prefix="look"):
    """Optional likeness evidence has no quality gate, but successful evidence must retain its bytes/identity."""
    if not look or look.get("status") != "ok":
        return copy.deepcopy(look)
    path, data = _read(root, look.get("report"), look.get("report_sha256", ""))
    doc = _document(data, "look comparison")
    if doc.get("schema") != "g3d.look_comparison/1" or doc.get("export_sha256") != glb_sha:
        raise DeliveryError("look comparison does not bind this export")
    if any(doc.get(key) != value for key, value in look.items() if key not in ("report", "report_sha256")):
        raise DeliveryError("look comparison differs from its saved report")
    paths = {}
    for asset in doc.get("assets", []):
        original, payload = _read(root, asset["path"], asset["sha256"])
        if not original.is_relative_to(path.parent):
            raise DeliveryError("look asset escapes comparison directory")
        name = prefix + "/" + original.relative_to(path.parent).as_posix()
        files[name], paths[str(original)] = payload, name
    converted = _map_paths(doc, paths)
    name = prefix + "/comparison-report.json"
    files[name] = _json_bytes(converted)
    return {**converted, "report": name, "report_sha256": _sha(files[name])}


def _package(root, result, exp, checkpoint, fingerprint, exit_head, summary, ar, model,
             capture, raw, shown, contact, *, source=None, selection=None, look=None, finish_images=(), owner_edit_report=None,
             final_delivery_failure=None, owner_applied=None):
    """Package evidence without changing a check's result. Drafts never enter the finish validator."""
    draft = source is not None
    files = {"model.glb": model}
    if capture:
        files["arview/report.json"] = capture["bytes"]
    for name, data in raw:
        files["arview/" + name] = data
    sheet_paths = {}
    for index, (path, data) in enumerate(contact, 1):
        suffix = path.suffix.lower()
        if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
            raise DeliveryError("unsupported AR contact sheet format")
        name = f"contact-sheets/{index:02d}{suffix}"
        files[name], sheet_paths[path] = data, name
    image_records = []
    for index, (record, path, data, image_source) in enumerate(shown, 1):
        name = sheet_paths.get(path)
        if name is None:
            suffix = path.suffix.lower()
            if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
                raise DeliveryError("unsupported AR tool image format")
            name = f"tool-images/{index:02d}{suffix}"
            files[name] = data
        if image_source not in sheet_paths:
            raise DeliveryError("AR tool image does not describe a contact sheet")
        image_records.append({**copy.deepcopy(record), "path": name, "source_path": sheet_paths[image_source],
                              "source_sha256": _sha(files[sheet_paths[image_source]])})
    ar_record = copy.deepcopy(ar)
    ar_record.pop("log", None)  # process logs are run evidence, not needed to inspect this immutable delivery
    ar_record.update(report="arview/report.json" if capture else None,
                     images=image_records, sheets=list(sheet_paths.values()))
    if "worn_views" in ar:
        ar_record["worn_views"] = []
        for rec in capture.get("data", {}).get("images", []):
            if rec.get("kind") != "worn_crop":
                continue
            name = "arview/" + _relative(rec["file"])
            if name not in files or _sha(files[name]) != rec["sha256"]:
                raise DeliveryError("worn crop was not copied into the immutable delivery")
            view = {**copy.deepcopy(rec), "path": name}
            if rec.get("source"):
                source_name = "arview/" + _relative(rec["source"])
                if source_name not in files:
                    raise DeliveryError("worn crop source is missing from the immutable delivery")
                view.update(source_path=source_name, source_sha256=_sha(files[source_name]))
            ar_record["worn_views"].append(view)
    if draft:
        files["arview/attempt.json"] = _json_bytes(ar_record)
    report = {"schema": REPORT_SCHEMA, "job_id": root.name, "run_id": result.get("run_id"),
              "checkpoint": checkpoint, "exit_checkpoint": copy.deepcopy(exit_head),
              "fingerprint": fingerprint, "glb_sha256": exp["sha256"],
              "validation_sha256": exp["validation_sha256"], "validate_spec": copy.deepcopy(exp["validate_spec"]),
              "seating": copy.deepcopy(exp["seat_check"]), "print_check": copy.deepcopy(exp["print_check"]),
              "arview": ar_record, "cost": owner_money(result.get("budget")),
              "summary": summary, "finished": not draft, "accepted_finish": not draft,
              "conversation_renewals": copy.deepcopy(result.get("renewals", [])),
              "stage_conversation_renewals": [{"stage": s.get("stage"), "call_id": s.get("call_id"),
                                               "renewals": copy.deepcopy(s.get("renewals", []))}
                                              for s in result.get("stages", []) if s.get("renewals")],
              "run_status": result.get("status")}
    if look is not None:
        report["look_comparison"] = _look_files(root, look, exp["sha256"], files)
    if look and look.get("status") == "ok":
        report["finish_images"] = []
        for index, image in enumerate(finish_images):
            path, data = _read(root, image["path"], image["sha256"])
            name = f"finish-images/{index:02d}{path.suffix.lower()}"
            files[name] = data
            report["finish_images"].append({"path": name, "sha256": _sha(data)})
    if draft:
        report.update(source_checkpoint=copy.deepcopy(source), checkpoint_selection=selection,
                      failed_checks=_failed_checks(report), stages=copy.deepcopy(result.get("stages", [])),
                      stage_progress=copy.deepcopy((result.get("error") or {}).get("stage_progress", [])))
        if final_delivery_failure is not None:
            report["final_delivery_failure"] = copy.deepcopy(final_delivery_failure)
    if "path" in report["validate_spec"]:
        report["validate_spec"]["path"] = "model.glb"
    if "glb" in report["seating"]:
        report["seating"]["glb"] = "model.glb"
    if owner_edit_report is not None:
        _, edit_bytes = _read(root, owner_edit_report["path"], owner_edit_report["sha256"])
        edit = _document(edit_bytes, "owner edit report")
        if edit.get("fingerprint") != fingerprint or edit.get("after", {}).get("export_sha256") != exp["sha256"]:
            raise DeliveryError("owner edit report does not describe this delivered checkpoint")
        edit["before"] = _look_files(root, edit["before"], edit["before"]["export_sha256"], files,
                                    prefix="owner-edit/before")
        edit["after"] = _look_files(root, edit["after"], exp["sha256"], files, prefix="owner-edit/after")
        images = []
        for i, rec in enumerate(edit["images"]):
            path, data = _read(root, rec["path"], rec.get("sha256"))
            name = f"owner-edit/images/{i:04d}{path.suffix}"
            files[name] = data
            images.append({**rec, "path": name, "sha256": _sha(data)})
        edit["images"] = images
        files["owner-edit/report.json"] = _json_bytes(edit)
        report["owner_edit_report"] = {"path": "owner-edit/report.json", "sha256": _sha(files["owner-edit/report.json"])}
    if owner_applied is not None:
        application = copy.deepcopy(owner_applied)
        for i, image in enumerate(application["images"]):
            _, payload = _read(root, image["path"], image["sha256"])
            name = f"owner-applied/{i:04d}.png"
            files[name] = payload
            image["path"] = name
        files["owner-applied/report.json"] = _json_bytes(application)
        report.update(accepted_finish=False, delivery_kind="owner_applied", tool_test=application["tool_test"],
                      owner_applied={"path": "owner-applied/report.json",
                                     "sha256": _sha(files["owner-applied/report.json"]),
                                     "source_basis": application.get("source_basis")})
    fallback = _full_edit_effects(root, result, exp, checkpoint, fingerprint, files)
    if fallback is not None:
        report.update(fallback)
    files["report.json"] = _json_bytes(report)
    manifest = {"schema": SCHEMA, "job_id": root.name, "run_id": result.get("run_id"),
                "glb_sha256": exp["sha256"], "model": "model.glb", "report": "report.json",
                "contact_sheets": list(sheet_paths.values()),
                "files": {name: {"sha256": _sha(data), "bytes": len(data)} for name, data in sorted(files.items())}}
    if fallback is not None:
        manifest.update(origin="full_edit_item", item_id=fallback["item_id"])
    manifest["id"] = _snapshot_id(manifest)
    return manifest, files


def _failed_checks(report):
    failed = [name for name in ("validate_spec", "seating", "print_check")
              if report[name].get("ok") is not True]
    if report["arview"].get("status") != "ok" or report["arview"].get("exit_code") != 0:
        failed.append("arview")
    return failed


def _resolved_report(directory: Path, report: dict) -> dict:
    report = copy.deepcopy(report)
    if report.get("side_effects_report"):
        report["side_effects_report"]["path"] = str(_inside(directory, report["side_effects_report"]["path"]))
    look = report.get("look_comparison")
    if look and look.get("status") == "ok":
        paths = {a["path"]: str(_inside(directory, a["path"])) for a in look["assets"]}
        paths[look["report"]] = str(_inside(directory, look["report"]))
        report["look_comparison"] = _map_paths(look, paths)
    for image in report.get("finish_images", []):
        image["path"] = str(_inside(directory, image["path"]))
    ar = report["arview"]
    if ar.get("report"):
        ar["report"] = str(_inside(directory, ar["report"]))
    ar["sheets"] = [str(_inside(directory, path)) for path in ar["sheets"]]
    for rec in ar["images"]:
        for field in ("path", "source_path"):
            rec[field] = str(_inside(directory, rec[field]))
    for rec in ar.get("worn_views", []):
        for field in ("path", "source_path"):
            if field in rec:
                rec[field] = str(_inside(directory, rec[field]))
    if "path" in report["validate_spec"]:
        report["validate_spec"]["path"] = str(directory / "model.glb")
    if isinstance(report.get("seating"), dict) and "glb" in report["seating"]:
        report["seating"]["glb"] = str(directory / "model.glb")
    return report


def load_delivery(root, delivery_id: str | None = None) -> dict:
    """Load a verified immutable snapshot, resolving current exactly once when no id is supplied.

    Pre-M4 snapshots may have seating=None. Their recorded evidence and identities remain immutable; only new
    publication requires an all-frame seating report; seating failure is diagnostic, not a finish refusal.
    """
    root = Path(root).resolve()
    pointer = None
    if delivery_id is None:
        _, data = _read(root, "deliveries/current.json")
        pointer = _document(data, "delivery pointer")
        if pointer.get("schema") != POINTER_SCHEMA:
            raise DeliveryError("invalid current delivery pointer")
        delivery_id = pointer.get("id")
    if not isinstance(delivery_id, str) or not _ID.fullmatch(delivery_id):
        raise DeliveryError("invalid delivery id")
    directory = _output_path(root, Path("deliveries") / delivery_id)
    manifest_path, manifest_bytes = _read(directory, "manifest.json",
                                         pointer.get("manifest_sha256", "") if pointer else None)
    manifest = _document(manifest_bytes, "delivery manifest")
    if (manifest_bytes != _json_bytes(manifest) or manifest.get("schema") != SCHEMA or manifest.get("id") != delivery_id
            or manifest.get("job_id") != root.name or _snapshot_id(manifest) != delivery_id
            or manifest.get("model") != "model.glb" or manifest.get("report") != "report.json"):
        raise DeliveryError("delivery manifest identity changed")
    records = manifest.get("files")
    if not isinstance(records, dict) or not {"model.glb", "report.json"} <= records.keys():
        raise DeliveryError("delivery manifest is incomplete")
    contents, folded = {}, set()
    for name, rec in records.items():
        _relative(name)
        if name.casefold() in folded or name.casefold() == "manifest.json" or not isinstance(rec, dict):
            raise DeliveryError("delivery manifest contains colliding file records")
        folded.add(name.casefold())
        _, data = _read(directory, name, rec.get("sha256", ""))
        if type(rec.get("bytes")) is not int or len(data) != rec["bytes"]:
            raise DeliveryError("delivery file size changed")
        contents[name] = data
    disk_files = set()
    for path in directory.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise DeliveryError("delivery contains a filesystem link")
        if path.is_file():
            disk_files.add(path.relative_to(directory).as_posix())
    if disk_files != set(records) | {"manifest.json"}:
        raise DeliveryError("delivery contains unlisted or missing files")
    sha = _sha(contents["model.glb"])
    report = _document(contents["report.json"], "delivery report")
    exit_head = report.get("exit_checkpoint") or {}
    validation, seating = report.get("validate_spec"), report.get("seating")
    printing = report.get("print_check")
    draft = report.get("finished") is False
    local_apply = report.get("delivery_kind") == "owner_applied"
    if "finished" in report and type(report["finished"]) is not bool:
        raise DeliveryError("delivery finished flag is invalid")
    if (manifest.get("glb_sha256") != sha or (pointer and pointer.get("glb_sha256") != sha)
            or report.get("schema") != REPORT_SCHEMA or report.get("job_id") != root.name
            or report.get("run_id") != manifest.get("run_id") or report.get("glb_sha256") != sha
            or report.get("validation_sha256") != sha or not isinstance(validation, dict)
            or validation.get("sha256") != sha
            or not report.get("checkpoint") or not report.get("fingerprint") or not isinstance(exit_head, dict)
            or (not draft and (validation.get("ok") is not True or exit_head.get("verified") is not True
                               or exit_head.get("id") != report.get("checkpoint")
                               or ("accepted_finish" in report and report["accepted_finish"] is not (not local_apply))
                               or ("run_status" in report and report["run_status"] !=
                                   ("owner_applied" if local_apply else "completed"))))
            or (seating is not None and (not isinstance(seating, dict)
                                         or (not draft and type(seating.get("ok")) is not bool)))
            or (printing is not None and (not isinstance(printing, dict)
                                         or (not draft and printing.get("ok") is not True)
                                         or printing.get("glb_sha256") != sha))):
        raise DeliveryError("delivery report does not bind the validated finished model")
    if local_apply:
        binding = report.get("owner_applied") or {}
        _, application_bytes = _read(directory, binding.get("path"), binding.get("sha256", ""))
        application = _document(application_bytes, "owner applied preview")
        if (draft or application.get("schema") != "g3d.owner_applied_preview/1"
                or application.get("status") != "applied" or application.get("api_requests") != 0
                or application.get("settled_usd") != "0.000000"
                or application.get("tool_test") is not report.get("tool_test")
                or type(report.get("tool_test")) is not bool
                or application.get("exact_preview_fingerprint") is not True
                or application.get("export_matches_preview_bytes") is not True
                or application.get("export", {}).get("sha256") != sha
                or application.get("preview_export_sha256") != sha
                or application.get("export", {}).get("fingerprint") != report["fingerprint"]
                or application.get("option", {}).get("fingerprint") != report["fingerprint"]
                or application.get("source_basis") != binding.get("source_basis")):
            raise DeliveryError("owner application does not bind its exact preview and delivered model")
        for image in application.get("images", []):
            _read(directory, image["path"], image["sha256"])
    if draft:
        source = report.get("source_checkpoint")
        if (report.get("accepted_finish") is not False
                or not _draft_eligible(report.get("run_status"), report.get("final_delivery_failure"))
                or report.get("checkpoint_selection") not in (
                    ("last_verified",) if report.get("run_status") in _REVERIFY_STATUSES else ("best", "head"))
                or not isinstance(source, dict) or source.get("verified") is not True
                or source.get("id") != report["checkpoint"] or source.get("fingerprint") != report["fingerprint"]
                or not isinstance(report["fingerprint"], str) or not _DIGEST.fullmatch(report["fingerprint"])
                or not isinstance(source.get("sha256"), str) or not _DIGEST.fullmatch(source["sha256"])
                or not isinstance(seating, dict) or not isinstance(printing, dict)
                or seating.get("glb_sha256") != sha
                or any(type(check.get("ok")) is not bool for check in (validation, seating, printing))
                or report.get("failed_checks") != _failed_checks(report)
                or contents.get("arview/attempt.json") != _json_bytes(report.get("arview"))):
            raise DeliveryError("draft report does not bind its source and reported checks")
        if report["run_status"] in _REVERIFY_STATUSES:
            attempts = source.get("verification_attempts")
            if (not isinstance(attempts, list) or not attempts
                    or not all(isinstance(attempt, dict) for attempt in attempts)
                    or any(attempt.get("status") != "verification_failed" or not attempt.get("reason")
                           for attempt in attempts[:-1])
                    or attempts[-1] != {"checkpoint": source["id"], "status": "verified",
                                        "sha256": source["sha256"], "fingerprint": source["fingerprint"]}):
                raise DeliveryError("failed draft does not retain its checkpoint verification attempts")
    elif "arview/report.json" not in records:
        raise DeliveryError("delivery manifest is incomplete")
    if report.get("origin") == "full_edit_item" or manifest.get("origin") == "full_edit_item":
        effects, binding = report.get("side_effects"), report.get("side_effects_report") or {}
        _, effects_bytes = _read(directory, binding.get("path"), binding.get("sha256", ""))
        if (manifest.get("origin") != report.get("origin") or manifest.get("item_id") != report.get("item_id")
                or not re.fullmatch(r"I[1-9][0-9]{0,8}", str(report.get("item_id", "")))
                or not isinstance(effects, dict) or effects_bytes != _json_bytes(effects)
                or effects.get("schema") != "g3d.full_edit_side_effects/1"
                or effects.get("informational_only") is not True or effects.get("item_id") != report["item_id"]
                or effects.get("delivered_glb_sha256") != sha
                or effects.get("delivered_fingerprint") != report["fingerprint"]):
            raise DeliveryError("full item edit side-effect report does not bind this delivered model and item")
        for name, record in effects.get("evidence", {}).items():
            if records.get(name) != record:
                raise DeliveryError("full item edit side-effect evidence differs from the immutable delivery")
    _capture_files(directory, report.get("arview"), sha, draft=draft)
    look = report.get("look_comparison")
    if look and look.get("status") == "ok":
        _, look_bytes = _read(directory, look["report"], look["report_sha256"])
        doc = _document(look_bytes, "look comparison")
        if (doc.get("export_sha256") != sha or
                any(doc.get(key) != value for key, value in look.items() if key not in ("report", "report_sha256"))):
            raise DeliveryError("packaged look comparison differs from the delivered export/report")
        for asset in look["assets"]:
            _read(directory, asset["path"], asset["sha256"])
    sheets = manifest.get("contact_sheets")
    if (sheets != report["arview"]["sheets"] or (not sheets and not draft)
            or any(path not in records for path in sheets)):
        raise DeliveryError("delivery contact sheets disagree with the report")
    resolved = _resolved_report(directory, report)
    fallback_fields = ({key: copy.deepcopy(resolved[key]) for key in
                        ("origin", "item_id", "side_effects", "side_effects_report")}
                       if report.get("origin") == "full_edit_item" else {})
    return {"status": "delivered", "id": delivery_id, "delivery_id": delivery_id, "glb_sha256": sha,
            **fallback_fields,
            "finished": not draft, "run_status": report.get("run_status", "completed"),
            "model": str(directory / "model.glb"), "report": str(directory / "report.json"),
            "contact_sheet": str(directory / sheets[0]) if sheets else None,
            "contact_sheets": [str(directory / p) for p in sheets],
            "manifest": str(manifest_path), "manifest_sha256": _sha(manifest_bytes), "report_data": resolved}


def _write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)


def _compatibility(root: Path, loaded: dict) -> None:
    """Publish a convenience copy; immutable APIs never consult these replaceable files."""
    dest = _output_path(root, "deliverable")
    dest.mkdir(exist_ok=True)
    for name, data in (("model.glb", Path(loaded["model"]).read_bytes()),
                       ("report.json", _json_bytes({**loaded["report_data"], "delivery_id": loaded["id"],
                                                    "manifest_sha256": loaded["manifest_sha256"]}))):
        path = _output_path(root, dest / name)
        temp = _inside(root, dest / f".{name}.{uuid4().hex}.tmp")
        _write_file(temp, data)
        procs.replace_with_retry(temp, path)


def deliver_finished(root, scene, result) -> dict:
    """Snapshot a successfully finished run. A rerun cannot overwrite a previous snapshot's bytes."""
    if result.get("status") != "completed" or not scene.finish:
        return {"status": "not_finished", "run_status": result.get("status")}
    root = Path(root).resolve()
    manifest, files = _prepare(root, scene, result)
    return _publish(root, manifest, files)


def deliver_owner_applied(root, export, exit_head, ar, application):
    """Publish a verified local owner choice with ordinary immutable review evidence.

    This is an owner application, never an Astra finish or a product acceptance.
    The same strict export, print, AR and verified-checkpoint conditions apply.
    """
    from types import SimpleNamespace
    root = Path(root).resolve()
    result = {"status": "owner_applied", "run_id": "owner-apply", "exit_checkpoint": exit_head,
              "budget": {"settled_micro_usd": 0, "requests": 0}}
    # Reuse the complete finished-export validation before packaging local authority.
    scene = SimpleNamespace(finish={"status": "accepted", "checkpoint": export.get("checkpoint"),
        "fingerprint": export.get("fingerprint"), "export": export}, last_ar=ar)
    _prepare(root, scene, result)
    _, model = _read(root, export["path"], export["sha256"])
    capture, raw, shown, contact = _capture_files(root, ar, export["sha256"])
    manifest, files = _package(root, result, export, export["checkpoint"], export["fingerprint"], exit_head,
        "Owner-selected preview applied exactly at $0; visual acceptance remains the owner's decision.",
        ar, model, capture, raw, shown, contact, owner_applied=application)
    return _publish(root, manifest, files)


def _publish(root, manifest, files):
    deliveries = _output_path(root, "deliveries")
    deliveries.mkdir(exist_ok=True)
    dest = _output_path(root, deliveries / manifest["id"])
    if not dest.exists():
        stage = _inside(root, deliveries / f".pending-{uuid4().hex}")
        stage.mkdir()
        try:
            for name, data in files.items():
                _write_file(_inside(stage, name), data)
            _write_file(stage / "manifest.json", _json_bytes(manifest))
            for delay in procs.REPLACE_DELAYS_S + (None,):
                try:
                    os.rename(stage, dest)
                    break
                except FileExistsError:  # another delivery won; verification below decides whether it is valid
                    break
                except PermissionError:
                    # Windows scanners can briefly hold the new directory. Keep rename's no-overwrite contract.
                    if delay is None:
                        raise
                    time.sleep(delay)
        finally:
            if stage.exists():
                # This is our freshly generated staging directory, resolved and confined before removing it.
                safe = _inside(deliveries, stage)
                shutil.rmtree(safe)
    loaded = load_delivery(root, manifest["id"])
    if loaded["manifest_sha256"] != _sha(_json_bytes(manifest)):
        raise DeliveryError("existing immutable delivery differs from this finished run")
    procs.write_json_atomic(_output_path(root, "deliveries/current.json"),
                           {"schema": POINTER_SCHEMA, "id": loaded["id"], "glb_sha256": loaded["glb_sha256"],
                            "manifest_sha256": loaded["manifest_sha256"]})
    try:
        _compatibility(root, loaded)
    except (OSError, DeliveryError) as exc:
        # Review/verdict resolve the immutable pointer. A viewer locking the optional convenience copy must not
        # strand that delivery, and returning its stale convenience path would misidentify the published model.
        return {**loaded, "snapshot_model": loaded["model"], "snapshot_report": loaded["report"],
                "compatibility": {"status": "unavailable", "reason": f"{type(exc).__name__}: {exc}"}}
    return {**loaded, "snapshot_model": loaded["model"], "snapshot_report": loaded["report"],
            "model": str(root / "deliverable/model.glb"), "report": str(root / "deliverable/report.json")}


def _last_summary(root: Path, result: dict) -> str | None:
    """Only public assistant text, never user/tool messages or encrypted reasoning."""
    final = result.get("final_output")
    if isinstance(final, str) and final.strip():
        return final
    transport = _inside(root, Path("runs") / _relative(result["run_id"]) / "transport")
    for path in sorted(transport.glob("*-response.json"), reverse=True):
        try:
            payload = _document(path.read_bytes(), "response")
        except (OSError, DeliveryError):
            continue
        output = payload.get("output")
        if not isinstance(output, list):
            continue
        for item in reversed(output):
            if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            text = "\n".join(p["text"] for p in content if isinstance(p, dict)
                             and p.get("type") == "output_text" and isinstance(p.get("text"), str))
            if text.strip():
                return text
    return None


def _draft_export(root: Path, scene, result: dict) -> tuple[dict, dict, str]:
    """Reopen copies without a session/journal, so head, best and history cannot move.

    Failed runs reverify this run's best first, then newest saved checkpoints if integrity fails. A failed export
    or quality check never causes selection of an older checkpoint: that would hide the last model's defects.
    """
    from .journal import CheckpointIndex
    from .worker_client import WorkerClient
    from .seeing import EXPORT_DEADLINE_S

    if getattr(scene.s, "worker", None) is not None:
        raise DeliveryError("run worker is still active; cannot select an immutable draft source")
    index = CheckpointIndex(root / "checkpoints")
    head = index.head
    expected = result.get("exit_checkpoint") or getattr(scene.s, "head", None) or {}
    if (not head or not expected.get("id") or head["id"] != expected["id"]
            or (expected.get("sha256") is not None and head.get("sha256") != expected["sha256"])):
        raise DeliveryError("no checkpoint, or the job head changed after this run ended")
    failed = result.get("status") in _REVERIFY_STATUSES
    best = getattr(scene, "best_checkpoint", None)
    if not result.get("run_id") or getattr(scene, "best_checkpoint_run_id", None) != result["run_id"]:
        best = None
    selected = index.get(best) if best else head
    if selected is None and failed:
        selected = head
    if selected is None:
        raise DeliveryError("the checkpoint marked best is missing")
    # Fatal failures still reopen and verify every candidate. Honour this run's
    # explicit best first; only failed integrity verification permits fallback.
    candidates = ([selected] + [row for row in reversed(index.checkpoints()) if row["id"] != selected["id"]]
                  if failed else [selected])
    attempts = []
    for selected in candidates:
        worker = None
        try:
            try:
                if not isinstance(selected.get("fingerprint"), str) or not _DIGEST.fullmatch(selected["fingerprint"]):
                    raise DeliveryError("checkpoint fingerprint is missing or invalid")
                blend, data = _read(root / "checkpoints", _relative(selected.get("file")), selected.get("sha256", ""))
                token = "draft-" + uuid4().hex
                work = _output_path(root, Path("work/drafts") / token)
                copied = work / "source.blend"
                _write_file(copied, data)
                worker = WorkerClient(root, start_blend=copied, worker_dir=work / "worker",
                                      blender=getattr(scene.s, "blender", None), selftest=getattr(scene.s, "selftest", False))
                worker.start()
                if worker.hello.get("fingerprint") != selected["fingerprint"]:
                    raise DeliveryError("draft worker did not open the selected checkpoint exactly")
                _read(root, blend, selected["sha256"])
            except Exception as exc:
                attempts.append({"checkpoint": selected.get("id"), "status": "verification_failed",
                                 "reason": f"{type(exc).__name__}: {exc}"})
                if not failed:
                    raise
                continue
            attempts.append({"checkpoint": selected["id"], "status": "verified",
                             "sha256": selected["sha256"], "fingerprint": selected["fingerprint"]})
            try:
                path = _output_path(root, Path("work/exports") / token / "model.glb")
                path.parent.mkdir(parents=True, exist_ok=False)
                export = worker.call("export_glb", {"path": str(path)}, deadline_s=EXPORT_DEADLINE_S)
                if (export.get("status") != "exported" or export.get("fingerprint_before") != selected["fingerprint"]
                        or export.get("fingerprint_after") != selected["fingerprint"] or export.get("unchanged") is not True):
                    raise DeliveryError("selected checkpoint has no unchanged usable export: " + str(export.get("message", "")))
                receipt = export.get("receipt") or {}
                if not receipt.get("object_count") or not receipt.get("triangle_count"):
                    raise DeliveryError("selected checkpoint has no exported geometry")
                if Path(export.get("path", "")).resolve() != path:
                    raise DeliveryError("draft export returned a different output path")
                _read(root, path, export.get("sha256", ""))
                _read(root, blend, selected["sha256"])
            except Exception as exc:
                if failed:
                    raise DraftSourceError(f"{type(exc).__name__}: {exc}", attempts) from exc
                raise
            source = {**selected, "verified": True}
            if failed:
                source["verification_attempts"] = attempts
            return ({**export, "checkpoint": selected["id"], "fingerprint": selected["fingerprint"],
                     "validation_sha256": export["sha256"]}, source, "last_verified" if failed else "best" if best else "head")
        finally:
            if worker is not None:
                worker.close()
    raise DraftSourceError("no checkpoint matched its original bytes and reopened fingerprint", attempts)


def _draft_checks(root, export):
    from ..g3d_host import seat_check, spec
    from . import print_freshness

    path, sha = export["path"], export["sha256"]
    for name, checker in (("validate_spec", lambda: spec.validate_spec(path)),
                          ("seat_check", lambda: seat_check.check_glb(path)),
                          ("print_check", lambda: print_freshness.check_current_print(path, root))):
        try:
            check = checker()
            if not isinstance(check, dict) or type(check.get("ok")) is not bool:
                raise ValueError("check did not return an explicit pass/fail result")
        except Exception as exc:
            # A readable draft survives a broken check, but its report explicitly records that failure.
            check = {"ok": False, "status": "check_failed", "error": f"{type(exc).__name__}: {exc}"}
        binding = "sha256" if name == "validate_spec" else "glb_sha256"
        if check.get(binding, sha) != sha:
            check = {"ok": False, "status": "check_failed", "error": "check describes different GLB bytes"}
        export[name] = {**check, binding: sha}
    _read(root, path, sha)


def _draft_capture(root, export, token):
    from .tools import arview

    base = {"export_sha256": export["sha256"], "glb_sha256": export["sha256"],
            "checkpoint": export["checkpoint"], "fixed_scale": True,
            "appearance_policy": arview.AR_APPEARANCE_POLICY, "lens_env_intensity": arview.LENS_ENV_INTENSITY,
            "images": [], "sheets": [], "report": None, "exit_code": None}
    try:
        ar = {**base, **arview.capture(root, export, token)}
        capture = _capture_files(root, ar, export["sha256"], draft=True)
        return ar, capture
    except Exception as exc:
        # Keep the host attempt rather than attributing an unavailable/invalid renderer report to this GLB.
        return ({**base, "status": "infrastructure_failed", "code": "E_DRAFT_CAPTURE",
                 "message": f"{type(exc).__name__}: {exc}"}, ({}, [], [], []))


def _draft_eligible(status, final_delivery_failure=None):
    return (status in DRAFT_STATUSES or
            (status == "completed" and isinstance(final_delivery_failure, dict)
             and isinstance(final_delivery_failure.get("reason"), str) and bool(final_delivery_failure["reason"])))


def deliver_draft(root, scene, result, *, final_delivery_failure=None) -> dict:
    """Host-only salvage of an unfinished run; no model call, accepted finish or checkpoint mutation."""
    from .budget import JobLedger, JobLedgerError

    if not _draft_eligible(result.get("status"), final_delivery_failure):
        return {"status": "not_finished", "run_status": result.get("status")}
    root = Path(root).resolve()
    refusal = {"status": "no_usable_checkpoint", "finished": False, "accepted_finish": False,
               "run_status": result["status"], "run_id": result.get("run_id"),
               "stages": copy.deepcopy(result.get("stages", [])),
               "stage_progress": copy.deepcopy((result.get("error") or {}).get("stage_progress", []))}
    if final_delivery_failure is not None:
        refusal["final_delivery_failure"] = copy.deepcopy(final_delivery_failure)
    try:
        # run_astra has released this lock. Reacquire before selection and hold through publication, preventing
        # resume from changing the source/evidence while a draft is exported, checked and captured.
        with JobLedger(root):
            try:
                export, source, selection = _draft_export(root, scene, result)
            except Exception as exc:
                evidence = {"checkpoint_attempts": exc.checkpoint_attempts} if isinstance(exc, DraftSourceError) else {}
                return {**refusal, "reason": f"{type(exc).__name__}: {exc}", **evidence}
            _draft_checks(root, export)
            ar, (capture, raw, shown, contact) = _draft_capture(root, export, "draft-" + uuid4().hex)
            _, model = _read(root, export["path"], export["sha256"])
            manifest, files = _package(root, result, export, source["id"], source["fingerprint"],
                                       result.get("exit_checkpoint") or {}, _last_summary(root, result),
                                       ar, model, capture, raw, shown, contact, source=source, selection=selection,
                                       final_delivery_failure=final_delivery_failure)
            return _publish(root, manifest, files)
    except JobLedgerError as exc:
        return {**refusal, "reason": str(exc)}
    except Exception as exc:
        # The source may be usable even when packaging/publication fails. Preserve an explicit failed attempt,
        # never label unavailable evidence a successful delivery or a certified check.
        return {**refusal, "status": "draft_failed", "reason": f"{type(exc).__name__}: {exc}"}
