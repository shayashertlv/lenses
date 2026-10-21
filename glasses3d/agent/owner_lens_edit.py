"""Owner-controlled lens values: direct GLB JSON patches and immutable $0 copies.

The source is never opened in Blender. A saved checkpoint is explicitly a GLB
snapshot, not a native scene checkpoint; its fingerprint is the GLB SHA-256.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
from uuid import uuid4

from . import delivery, edits, jobs, procs, review

SCHEMA = "g3d.owner_lens_edit/1"
RUNTIME_ROOT = Path(__file__).resolve().parents[1] / "data/owner-lens-edits"
FIELDS = {"tint", "transmission", "metallic", "roughness", "iridescence", "ior",
          "iridescence_ior", "iridescence_thickness_min", "iridescence_thickness_max"}
EXTENSIONS = ("KHR_materials_transmission", "KHR_materials_ior", "KHR_materials_iridescence")


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _payload(source):
    return source if isinstance(source, bytes) else Path(source).read_bytes()


def _parse(payload):
    """Retain the non-JSON chunks, including their original framing and padding."""
    if len(payload) < 20 or struct.unpack_from("<4sII", payload) != (b"glTF", 2, len(payload)):
        raise ValueError("The model is not a complete version 2 GLB.")
    offset, chunks = 12, []
    while offset < len(payload):
        if offset + 8 > len(payload):
            raise ValueError("The model has an incomplete GLB chunk.")
        length, kind = struct.unpack_from("<II", payload, offset)
        end = offset + 8 + length
        if length % 4 or end > len(payload):
            raise ValueError("The model has an invalid GLB chunk length.")
        chunks.append((kind, payload[offset + 8:end], payload[offset:end]))
        offset = end
    if not chunks or chunks[0][0] != 0x4E4F534A or sum(k == 0x4E4F534A for k, _, _ in chunks) != 1:
        raise ValueError("The model needs exactly one first JSON chunk.")

    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("The model has duplicate JSON keys.")
            out[key] = value
        return out

    def invalid(value):
        raise ValueError("The model has a non-finite JSON value: " + value)

    try:
        doc = json.loads(chunks[0][1].decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("The model JSON is unreadable.") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("materials"), list):
        raise ValueError("The model has no material list.")
    return doc, chunks


def _role(value):
    # Match AR's explicit-role sites and spelling/legacy interpretation. Name
    # and transmission fallbacks deliberately grant no owner-edit authority.
    from glasses3d.g3d_host.lens_id import _explicit_role, role_says_lens
    extras = value.get("extras") if isinstance(value, dict) else None
    if isinstance(extras, dict) and any(key in extras and extras[key] is not None
            and not isinstance(extras[key], str) for key in ("partRole", "part_role")):
        return "invalid-role"
    role, key = _explicit_role(extras)
    return None if role is None else "lens" if role_says_lens(role, key) else role.strip().lower()


def _active_nodes(doc):
    """The same default-scene reachability used by AR's enumerateGltfUses."""
    nodes = doc.get("nodes", [])
    scenes = doc.get("scenes")
    if isinstance(scenes, list) and scenes:
        scene_index = doc.get("scene", 0)
        scene = scenes[scene_index] if type(scene_index) is int and 0 <= scene_index < len(scenes) else {}
        roots = scene.get("nodes", []) if isinstance(scene, dict) else []
    else:
        children = {index for node in nodes if isinstance(node, dict)
                    for index in node.get("children", []) if type(index) is int}
        roots = [index for index in range(len(nodes)) if index not in children]
    active, pending = set(), list(roots)
    while pending:
        index = pending.pop()
        if type(index) is not int or not 0 <= index < len(nodes) or index in active:
            continue
        active.add(index)
        node = nodes[index]
        if isinstance(node, dict):
            pending.extend(node.get("children", []))
    return active


def _lens_indices(doc):
    materials, uses = doc["materials"], {}
    node_roles, active_materials = {}, set()
    active = _active_nodes(doc)
    for node_index, node in enumerate(doc.get("nodes", [])):
        if "mesh" in node:
            node_roles.setdefault(node["mesh"], []).append((_role(node), node_index in active))
    for mesh_index, mesh in enumerate(doc.get("meshes", [])):
        for primitive in mesh.get("primitives", []):
            index = primitive.get("material")
            if type(index) is not int or not 0 <= index < len(materials):
                continue
            # A mesh may be instanced by several nodes with different roles.
            # Every actual node/primitive use must prove lens ownership on its
            # own; a labelled sibling cannot lend its role to an unlabelled use.
            for node_role, visible in node_roles.get(mesh_index, [(None, False)]):
                roles = {_role(materials[index]), _role(mesh), node_role} - {None}
                uses.setdefault(index, []).append(roles)
                if visible and primitive.get("mode", 4) in (4, 5, 6):
                    active_materials.add(index)
    # Require a declared lens role on every use. In particular, a shared
    # frame/lens material cannot accidentally recolour the frame.
    return [index for index, rows in uses.items()
            if index in active_materials and rows and all(row == {"lens"} for row in rows)]


def _number(value, low, high, label):
    bounds = f"at least {low:g}" if high is None else f"from {low:g} to {high:g}"
    message = f"{label} must be a finite number {bounds}."
    if type(value) not in (int, float):
        raise ValueError(message)
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(message) from exc
    if not math.isfinite(number) or number < low or (high is not None and number > high):
        raise ValueError(message)
    return number


def _values(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("Provide every lens control value, with no unknown fields.")
    tint = value["tint"]
    if not isinstance(tint, list) or len(tint) != 3:
        raise ValueError("Tint must contain three linear red, green and blue values.")
    result = {"tint": [_number(v, 0, 1, "Tint") for v in tint]}
    for field in ("transmission", "metallic", "roughness", "iridescence"):
        result[field] = _number(value[field], 0, 1, field)
    # Khronos permits any IOR >= 1, plus 0 for the lens's specular-glossiness
    # compatibility mode. Film IOR has no zero exception and neither has a cap.
    result["ior"] = _number(value["ior"], 0, None, "ior")
    if 0 < result["ior"] < 1:
        raise ValueError("ior must be zero or a finite number at least 1.")
    result["iridescence_ior"] = _number(value["iridescence_ior"], 1, None, "iridescence_ior")
    # Thickness endpoints are nonnegative, uncapped and may be reversed to
    # invert a thickness texture. The panel's slider range is only a UI aid.
    for field in ("iridescence_thickness_min", "iridescence_thickness_max"):
        result[field] = _number(value[field], 0, None, field)
    return result


def _material_values(material):
    pbr, extensions = material.get("pbrMetallicRoughness", {}), material.get("extensions", {})
    film = extensions.get("KHR_materials_iridescence", {})
    return _values({"tint": pbr.get("baseColorFactor", [1, 1, 1, 1])[:3],
        "transmission": extensions.get("KHR_materials_transmission", {}).get("transmissionFactor", 0),
        "metallic": pbr.get("metallicFactor", 1), "roughness": pbr.get("roughnessFactor", 1),
        "ior": extensions.get("KHR_materials_ior", {}).get("ior", 1.5),
        "iridescence": film.get("iridescenceFactor", 0), "iridescence_ior": film.get("iridescenceIor", 1.3),
        "iridescence_thickness_min": film.get("iridescenceThicknessMinimum", 100),
        "iridescence_thickness_max": film.get("iridescenceThicknessMaximum", 400)})


def read_lens_materials(source_glb):
    """Return UI values in glTF linear RGB, never infer lens ownership from tint."""
    payload = _payload(source_glb)
    doc, _ = _parse(payload)
    rows = [{"material_index": index, "name": doc["materials"][index].get("name", f"Lens {index + 1}"),
             "values": _material_values(doc["materials"][index])} for index in sorted(_lens_indices(doc))]
    if not rows:
        raise ValueError("This model has no materials used only by parts labelled lens.")
    return {"schema": SCHEMA, "glb_sha256": _sha(payload), "materials": rows}


def patch_lens_glb(source_glb, values_by_material):
    """Return patched bytes and an exact preservation receipt; never write source."""
    original = _payload(source_glb)
    doc, chunks = _parse(original)
    lenses = set(_lens_indices(doc))
    if not isinstance(values_by_material, dict) or not values_by_material:
        raise ValueError("Choose at least one lens material and provide all its values.")
    values = {}
    for key, row in values_by_material.items():
        if type(key) is int:
            index = key
        elif isinstance(key, str) and re.fullmatch(r"0|[1-9][0-9]*", key):
            index = int(key)
        else:
            raise ValueError("Each lens material needs its exact material index.")
        if index not in lenses or index in values:
            raise ValueError("Only distinct materials used exclusively by labelled lens parts may be edited.")
        values[index] = _values(row)
    changed, after = [], copy.deepcopy(doc)
    for index, value in values.items():
        mat = after["materials"][index]
        old = _material_values(mat)
        if old == value:
            continue
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorFactor"] = [*value["tint"], pbr.get("baseColorFactor", [1, 1, 1, 1])[3]]
        pbr.update(metallicFactor=value["metallic"], roughnessFactor=value["roughness"])
        ext = mat.setdefault("extensions", {})
        ext.setdefault(EXTENSIONS[0], {})["transmissionFactor"] = value["transmission"]
        ext.setdefault(EXTENSIONS[1], {})["ior"] = value["ior"]
        ext.setdefault(EXTENSIONS[2], {}).update(iridescenceFactor=value["iridescence"],
            iridescenceIor=value["iridescence_ior"], iridescenceThicknessMinimum=value["iridescence_thickness_min"],
            iridescenceThicknessMaximum=value["iridescence_thickness_max"])
        changed.append(index)
    if changed:
        used = after.setdefault("extensionsUsed", [])
        if not isinstance(used, list) or any(not isinstance(v, str) for v in used):
            raise ValueError("The model extension list is invalid.")
        for name in EXTENSIONS:
            if name not in used:
                used.append(name)
        encoded = json.dumps(after, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        encoded += b" " * (-len(encoded) % 4)
        body = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + b"".join(raw for _, _, raw in chunks[1:])
        patched = struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body
    else:
        patched = original
    receipt = {"schema": "g3d.lens_patch/1", "source_glb_sha256": _sha(original),
        "glb_sha256": _sha(patched), "values": {str(i): values[i] for i in sorted(values)},
        "changed_material_indices": sorted(changed), "binary_chunks_unchanged": True,
        "unrelated_json_unchanged": True, "non_json_chunks": [
            {"type": kind, "bytes": len(raw), "sha256": _sha(raw)} for kind, _, raw in chunks[1:]],
        "serialization": "Only JSON formatting, the JSON chunk length, total length, lens values and required extension declarations change."}
    return patched, receipt


def _source(source_root, delivery_id):
    root = Path(source_root).resolve()
    loaded = delivery.load_delivery(root, delivery_id)
    model = Path(loaded["model"]).read_bytes()
    if _sha(model) != loaded["glb_sha256"]:
        raise ValueError("The source model changed while it was being read.")
    return root, loaded, model


def _destination(source_root, destination, prefix, operation):
    target = Path(destination).resolve() if destination is not None else RUNTIME_ROOT / (prefix + uuid4().hex[:12])
    if target.is_relative_to(source_root) or source_root.is_relative_to(target):
        raise ValueError("Choose a new destination outside the unchanged source job.")
    if target.exists():
        marker = target / "lens-operation.json"
        if not marker.is_file() or edits.read(marker) != operation:
            raise ValueError("This destination belongs to a different lens edit. Choose a new destination.")
        if _completed(target, operation):
            return target, True
        # A worker can die after publishing a retry and before the task registry
        # learns its path. Rediscover only a complete sibling with this exact
        # source/values binding, so a restart never republishes that result.
        retry_prefix = target.name + "-retry-"
        for candidate in sorted(target.parent.iterdir()):
            if (not candidate.name.startswith(retry_prefix) or not candidate.is_dir()
                    or candidate.is_symlink() or candidate.resolve() != candidate.absolute()):
                continue
            marker = candidate / "lens-operation.json"
            if marker.is_file() and edits.read(marker) == operation and _completed(candidate, operation):
                return candidate, True
        # Preserve interrupted evidence. Only our bound operation can retry,
        # and it gets a fresh sibling rather than overwriting partial work.
        target = target.with_name(target.name + "-retry-" + uuid4().hex[:8])
    target.mkdir(parents=True, exist_ok=False)
    procs.write_json_atomic(target / "lens-operation.json", operation)
    return target, False


def _completed(target, operation):
    if operation["kind"] == "save" and (target / "deliveries/current.json").exists():
        if delivery.load_delivery(target)["glb_sha256"] != operation["glb_sha256"]:
            raise ValueError("The completed lens edit differs from these values.")
        return True
    if operation["kind"] == "preview" and (target / "preview.json").exists():
        if edits.sha(target / "model.glb") != operation["glb_sha256"]:
            raise ValueError("The completed lens preview changed.")
        return True
    return False


def _operation(kind, loaded, receipt):
    return {"schema": "g3d.lens_operation/1", "kind": kind,
        "source_manifest_sha256": loaded["manifest_sha256"], "source_glb_sha256": loaded["glb_sha256"],
        "glb_sha256": receipt["glb_sha256"], "values": receipt["values"]}


def _source_unchanged(root, loaded):
    current = delivery.load_delivery(root, loaded["delivery_id"])
    if (current["manifest_sha256"], current["glb_sha256"]) != (loaded["manifest_sha256"], loaded["glb_sha256"]):
        raise ValueError("The source delivery changed while the lens copy was prepared.")


def create_preview(source_root, delivery_id, values_by_material, destination=None, *, open_ar=True,
                   _patcher=None):
    """Create a temporary GLB, not a delivery; optionally open verified detached AR."""
    source_root, loaded, original = _source(source_root, delivery_id)
    patched, receipt = (_patcher or patch_lens_glb)(original, values_by_material)
    root, reused = _destination(source_root, destination, "preview-", _operation("preview", loaded, receipt))
    model = root / "model.glb"
    if not reused:
        model.write_bytes(patched)
        procs.write_json_atomic(root / "preview.json", {"schema": "g3d.lens_preview/1", "temporary": True,
            "source_delivery_id": loaded["delivery_id"], "patch": receipt})
    _source_unchanged(source_root, loaded)
    result = {"root": str(root), "model": str(model), "model_path": str(model),
              "glb_sha256": receipt["glb_sha256"], "temporary": True, "patch": receipt}
    if open_ar:
        node = shutil.which("node")
        if not node:
            raise ValueError("Node.js is required to check this preview in AR.")
        launch = root / "ar" / uuid4().hex
        launch.mkdir(parents=True)
        state = None
        try:
            state, state_path, method, log = review._serve(node, model, 0, launch)
            report = review._verify(state, model, receipt["glb_sha256"], 0, method, draft=True)
            _source_unchanged(source_root, loaded)
            result.update(url=state["url"], server_state=str(state_path), startup_log=str(log),
                          warnings=report.get("warnings", []))
            procs.write_json_atomic(root / "ar-preview.json", result)
        except BaseException:
            if isinstance(state, dict):
                review._stop_owned(state)
            raise
    return result


def save_lens_edit(source_root, delivery_id, values_by_material, destination=None, *, tool_test=False,
                   _patcher=None, _look_record=None):
    """Publish an exact local preview through the existing owner-apply verifier.

    Invalid source drafts remain available to preview but cannot gain a successful
    finished-delivery claim. Strict spec, seating, retained print and AR checks
    run afresh on the edited GLB.
"""
    source_root, loaded, original = _source(source_root, delivery_id)
    patched, receipt = (_patcher or patch_lens_glb)(original, values_by_material)
    jobs.load_job(source_root)
    previous = loaded["report_data"]
    operation = {**_operation("save", loaded, receipt), "tool_test": bool(tool_test)}
    if _look_record is not None:
        operation["owner_look"] = copy.deepcopy(_look_record)
    root, reused = _destination(source_root, destination, "lens-", operation)
    if reused:
        _source_unchanged(source_root, loaded)
        return root
    # Copy only owner inputs; never create or alter source runs, verdicts or history.
    shutil.copyfile(source_root / "job.json", root / "job.json")
    for name in ("images", "reference_images"):
        if (source_root / name).exists():
            expected = edits.tree_hashes(source_root / name)
            shutil.copytree(source_root / name, root / name)
            if edits.tree_hashes(root / name) != expected:
                raise ValueError("The copied reference photos differ from the source.")
    work = root / "work/lens-edit"
    work.mkdir(parents=True)
    model = work / "preview.glb"
    model.write_bytes(patched)
    from glasses3d.g3d_host.spec import validate_spec
    validation = validate_spec(model)
    if validation.get("ok") is not True:
        raise ValueError("The lens copy did not pass the existing model specification check; no new delivery was published.")
    digest = receipt["glb_sha256"]
    checkpoint_id = "lens-" + digest[:16]
    checkpoint = {"kind": "owner_lens_glb", "verified": True, "id": checkpoint_id,
                  "sha256": digest, "fingerprint": digest, "fingerprint_kind": "glb_sha256",
                  "file": checkpoint_id + ".glb"}
    checkpoint_dir = root / "checkpoints"
    checkpoint_dir.mkdir()
    (checkpoint_dir / checkpoint["file"]).write_bytes(patched)
    procs.write_json_atomic(checkpoint_dir / "lens-checkpoint.json", checkpoint)
    from glasses3d.g3d_host.seat_check import check_glb
    from . import print_freshness
    seating = check_glb(model)
    print_freshness.carry_evidence(source_root, root)
    printing = print_freshness.check_current_print(model, root)
    if printing.get("ok") is not True:
        raise ValueError("The lens copy did not pass the existing retained-print check; no new delivery was published.")
    export = {"status": "exported", "path": str(model), "sha256": digest,
        "checkpoint": checkpoint_id, "fingerprint": digest, "fingerprint_kind": "glb_sha256",
        "validation_sha256": digest, "validate_spec": validation, "seat_check": seating, "print_check": printing}
    from .tools import arview
    ar = arview.capture(root, export, "owner-lens-ar")
    application = {"schema": "g3d.owner_applied_preview/1", "status": "applied", "origin": "owner_lens_edit",
        "tool_test": bool(tool_test), "visual_acceptance": False, "api_requests": 0, "settled_usd": "0.000000",
        "exact_preview_fingerprint": True, "export_matches_preview_bytes": True,
        "preview_export_sha256": digest, "option": {"id": "owner-lens-values", "fingerprint": digest},
        "export": export, "images": [], "source_basis": None,
        "confirmation_text": "Dev UI: owner pressed Save as new version for these lens values.",
        "owner_lens_edit": {"schema": SCHEMA, "created_utc": procs.utcnow(), "patch": receipt,
            "source": {"job": str(source_root), "delivery_id": loaded["delivery_id"],
                       "glb_sha256": loaded["glb_sha256"], "manifest_sha256": loaded["manifest_sha256"],
                       "checkpoint": previous.get("checkpoint"), "fingerprint": previous.get("fingerprint")},
            "checkpoint_kind": "owner_lens_glb", "native_scene_available": False}}
    if _look_record is not None:
        application.update(origin="owner_look_edit", owner_look_edit=copy.deepcopy(_look_record),
                           confirmation_text="Dev UI: owner saved these appearance values as a new version.")
    if model.read_bytes() != patched or (checkpoint_dir / checkpoint["file"]).read_bytes() != patched:
        raise ValueError("The lens preview changed before saving.")
    _source_unchanged(source_root, loaded)
    procs.write_json_atomic(work / "owner-lens-edit.json", application)
    delivery.deliver_owner_applied(root, export, checkpoint, ar, application)
    return root
