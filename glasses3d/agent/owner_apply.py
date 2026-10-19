"""Apply an already shown, sealed owner choice locally, without an inference round."""
from __future__ import annotations

import html
from pathlib import Path

from . import delivery, edit_capabilities, edits, owner_edits, procs
from .job_scene import JobScene


def _choice(root, doc):
    binding = doc["understanding"]["amount_preview"]
    preview = edits.read(edits.confined(root, binding["preview_receipt"]))
    choice = next(row for row in preview["options"] if row["id"] == doc["selected_preview_option"])
    # All evidence was verified by edits.load; resolve copies by hash, never by the
    # original preview's absolute paths (which can point at another job).
    paths = {}
    for name, digest in preview["evidence_files"].items():
        if name.endswith(".png"):
            paths.setdefault(digest, edits.confined(root, name))
    images = []
    for image in [*choice.get("full_pairs", []), *choice.get("target_details", [])]:
        path = paths.get(image["sha256"])
        if path is None or edits.sha(path) != image["sha256"]:
            raise ValueError("selected preview image is missing or changed")
        images.append({**image, "path": str(path)})
    if not images:
        raise ValueError("selected preview has no photo/before/after evidence")
    return choice, images


def _page(root, report):
    esc = html.escape
    label = "TOOL TEST on a copy" if report["tool_test"] else "Owner-selected edit on a copy"
    body = ["<!doctype html><meta charset='utf-8'><title>Applied preview</title>",
            "<style>body{font:18px system-ui;margin:2em;max-width:1800px}img{max-width:100%}</style>",
            f"<h1>{label}: {esc(report['option']['id'])}</h1>",
            "<p>Exact shown operations applied. 0 API requests; $0. "
            "Visual acceptance remains the owner's decision.</p>",
            "<p>The photo-first comparisons are the sealed selected preview; the applied model "
            "has the exact same verified scene fingerprint.</p>"]
    for image in report["images"]:
        relative = Path("../..") / Path(image["path"]).relative_to(root)
        body.append(f"<figure><figcaption>{esc(image.get('view', 'Photo / before / selected result'))}</figcaption>"
                    f"<a href='{esc(relative.as_posix())}'><img src='{esc(relative.as_posix())}'></a></figure>")
    path = root / "work/owner_edit/applied-preview.html"
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def apply_preview(edit_root, confirmation_text, option_id, *, destination=None, tool_test=False):
    """Confirm an exact option and apply it to a fresh copy; never invoke a paid runner.

    tool_test labels the owner's authorization to demonstrate an option. It does
    not claim they selected that amount as the final product or accepted its look.
    """
    if not isinstance(option_id, str) or not option_id.strip():
        raise ValueError("an exact shown preview option is required")
    root = edits.create_edit(edit_root, confirmation_text, option_id=option_id, destination=destination, record=False)
    doc = edits.load(root)
    if not doc.get("selected_preview"):
        raise ValueError("offline apply requires an existing sealed rendered option")
    edit_capabilities.require_round(root, doc)
    choice, images = _choice(root, doc)
    scene = JobScene(root)
    try:
        scene.start()
        source_head = dict(scene.s.head)
        if (source_head["fingerprint"] != doc["source"]["fingerprint"] or
                scene.s.fingerprint() != doc["source"]["fingerprint"]):
            raise ValueError("offline apply must start from the exact unchanged selected source")
        operation_ids = [op["id"] for op in doc["selected_operations"]]
        applied = owner_edits.apply(scene, operation_ids, "host-preview-apply")
        if applied.get("status") != "ok":
            raise ValueError("selected preview apply refused: " + str(applied))
        if scene.s.fingerprint() != doc["selected_preview"]["fingerprint"]:
            raise ValueError("applied scene differs from the exact selected preview")
        scope = owner_edits.ScopeGuard(scene.owner_edit_authority).verify(scene)
        if not scope.get("ok"):
            raise ValueError("selected preview failed scope verification: " + str(scope))
        exported = scene.seeing.export_head("host-preview-export")
        if exported.get("status") != "exported" or not exported.get("validate_spec", {}).get("ok"):
            raise ValueError("selected preview failed strict export validation")
        if exported.get("fingerprint") != doc["selected_preview"]["fingerprint"]:
            raise ValueError("export does not belong to the exact selected preview")
        if not exported.get("print_check", {}).get("ok"):
            raise ValueError("selected preview failed retained print verification")
        if exported["sha256"] != choice["export"]["sha256"]:
            raise ValueError("applied export bytes differ from the shown preview")
        # A concurrent modification of copied authority/evidence cannot be reported
        # as successful just because an earlier load passed.
        final_doc = edits.load(root)
        if final_doc != doc:
            raise ValueError("selected preview authority changed during application")
        edit_capabilities.require_round(root, final_doc)
        report = {"schema": "g3d.owner_applied_preview/1", "status": "applied", "tool_test": bool(tool_test),
                  "visual_acceptance": False, "api_requests": 0, "settled_usd": "0.000000",
                  "request_text": doc["request_text"], "confirmation_text": confirmation_text,
                  "option": doc["selected_preview"], "source_head": source_head,
                  "checkpoint": scene.s.head, "exact_preview_fingerprint": True,
                  "apply_receipt": applied, "scope_check": scope, "export": exported,
                  "preview_export_sha256": choice["export"]["sha256"],
                  "export_matches_preview_bytes": exported["sha256"] == choice["export"]["sha256"],
                  "images": images, "image_provenance": "sealed selected preview, exact applied fingerprint",
                  "round_path": str(root)}
        basis_id = doc["source"]["basis"]
        report["source_basis"] = {"basis": basis_id,
            "basis_sha256": edits.sha(edits.confined(root, Path("cameras") / basis_id / "basis.json"))}
        from .tools import arview
        ar = arview.capture(root, exported, "host-preview-ar")
        exit_head = scene.exit_checkpoint()
        published = delivery.deliver_owner_applied(root, exported, exit_head, ar, report)
        path = root / "work/owner_edit/applied-preview.json"
        procs.write_json_atomic(path, report)
        page = _page(root, report)
        if destination is None:
            edits.record_round(edit_root, root)
        return {**report, "report": str(path), "review_page": str(page), "delivery": published}
    finally:
        scene.close()


def verify_choice_exactness(source_snapshot, selected, current_snapshot, exported_sha256):
    """Compare composed picks with their sealed native snapshots, without tolerances.

    Positions are the worker's stored float32 local coordinates (and their world
    representation); normals, UV rows and material data are compared exactly.
    Derived position/topology hashes and evaluated summaries are not geometry.
    A topology or scalar/material change conservatively touches its whole object.
    Overlap is reported, never called preview equality: the caller must separately
    verify each ordered operation and the final scope/physical/export checks.
    """
    import math
    from ..g3d import owner_mesh

    if not isinstance(selected, list) or not selected:
        raise ValueError("Choose at least one sealed option to compare with its preview.")
    source = owner_mesh.objects(source_snapshot)
    current = owner_mesh.objects(current_snapshot)
    if set(source) != set(current) or source_snapshot.get("origin_mm") != current_snapshot.get("origin_mm"):
        raise ValueError("Applied picks changed the source objects or bridge origin.")
    previews = [owner_mesh.objects(row["snapshot"]) for row in selected]
    if any(set(objects) != set(source) for objects in previews):
        raise ValueError("A selected preview has different source object identities.")
    if len(selected) == 1:
        pick = selected[0]
        preview_fp = pick["export"].get("fingerprint")
        if (not preview_fp or pick["snapshot"].get("fingerprint") != preview_fp
                or current_snapshot.get("fingerprint") != preview_fp or
                exported_sha256 != pick["export"].get("sha256")):
            raise ValueError("Applied export bytes or scene fingerprint differ from the selected preview.")

    ignored = {"positions_sha256", "topology_sha256", "evaluated", "normal_intent",
               "material_sha256", "other_attributes_sha256"}
    whole = ("whole_object",)
    intent_name, mask_name = "g3d_owner_normal_intent", "g3d_owner_normal_managed"
    auto_name = "g3d_owner_normal_automatic"

    def named_layers(obj):
        layers = {}
        for key in ("attributes", "normal_attributes"):
            values = obj.get(key, [])
            if not isinstance(values, list):
                raise ValueError("Snapshot attributes must be named layer lists.")
            layers[key] = {}
            for layer in values:
                name = layer.get("name") if isinstance(layer, dict) else None
                if not isinstance(name, str) or not name or name in layers[key]:
                    raise ValueError("Snapshot attributes need distinct layer names.")
                layers[key][name] = layer
        if set(layers["attributes"]) & set(layers["normal_attributes"]):
            raise ValueError("Snapshot attributes need distinct layer names.")
        return layers

    source_layers = {oid: named_layers(obj) for oid, obj in source.items()}
    implicit_normal_types = {}
    for oid, original in source.items():
        # Only a sealed owner preview can introduce an implicit storage format.
        # The applied output must not select its own comparison baseline.
        implicit_normal_types[oid] = None
        if "custom_normal" in source_layers[oid]["normal_attributes"]:
            continue
        for objects in previews:
            obj = objects[oid]
            layers = named_layers(obj)
            raw = layers["normal_attributes"].get("custom_normal")
            witness = obj.get("normal_intent")
            if (raw is not None and intent_name in layers["attributes"]
                    and isinstance(witness, dict)
                    and all(witness.get(flag) is True for flag in
                            ("encoded_matches", "normal_space_matches", "decoded_within_quantization"))):
                implicit_normal_types[oid] = raw.get("data_type")
                break

    def attribute_layers(obj, oid):
        """Named values with exact defaults only for new owner normal layers.

        A layer present on the source is never synthesized after removal. Its
        deletion or a real metadata/type change therefore remains whole-object.
        Legacy intent without a managed mask retains its encoding-witness check,
        but absence of a mask always means false here, never new edit authority.
        No source or preview dictionary is mutated by this normalization.
        """
        layers = named_layers(obj)
        attrs, normals = layers["attributes"], layers["normal_attributes"]
        original = source[oid]
        original_layers = source_layers[oid]
        count = len(obj.get("corner_normals", []))
        source_count = len(original.get("corner_normals", []))
        had_intent, had_mask = intent_name in attrs, mask_name in attrs
        had_auto = auto_name in attrs
        had_encoded = "custom_normal" in normals
        if "has_custom_normals" in obj:
            if type(obj["has_custom_normals"]) is not bool or obj["has_custom_normals"] != had_encoded:
                raise ValueError("Custom-normal storage flag differs from its encoded attribute.")

        def known(key, name, domain, data_type, rows, *, field=None, allow_default=True):
            collection = layers[key]
            metadata = {"name": name, "domain": domain, "data_type": data_type}
            if field is not None:
                metadata["field"] = field
            if name not in collection:
                if not allow_default or name in original_layers[key]:
                    return None
                collection[name] = {**metadata, "values": rows}
            layer = collection[name]
            if ({key: value for key, value in layer.items() if key != "values"} != metadata
                    or not isinstance(layer.get("values"), list) or len(layer["values"]) != len(rows)):
                raise ValueError("Normal-storage attributes have invalid metadata or row counts.")
            return layer["values"]

        # Missing intent/free storage uses the authenticated source vectors, not
        # the applied output's decoded vectors. Unchanged layout is required for
        # this implicit correspondence; changed topology stays whole-object.
        intent = known("attributes", intent_name, "CORNER", "FLOAT_VECTOR",
                       obj.get("corner_normals", []) if had_intent else original.get("corner_normals", []),
                       allow_default=count == source_count)
        mask = known("attributes", mask_name, "CORNER", "BOOLEAN", [[False] for _ in range(count)])
        automatic = known("attributes", auto_name, "CORNER", "BOOLEAN", [[False] for _ in range(count)])
        sharp = known("attributes", "sharp_edge", "EDGE", "BOOLEAN", [[False] for _ in obj.get("edges", [])])
        encoded_type = normals["custom_normal"].get("data_type") if had_encoded else implicit_normal_types[oid]
        encoded = None
        if had_encoded or encoded_type is not None:
            if encoded_type == "INT16_2D":
                default_encoded, field = [[0, 0] for _ in range(count)], "value"
            elif encoded_type == "FLOAT_VECTOR":
                default_encoded, field = (obj.get("corner_normals", []) if had_encoded
                                          else original.get("corner_normals", [])), "vector"
            else:
                raise ValueError("Custom-normal storage has an unsupported encoded format.")
            encoded = known("normal_attributes", "custom_normal", "CORNER", encoded_type,
                            default_encoded, field=field,
                            allow_default=encoded_type != "FLOAT_VECTOR" or count == source_count)

        def finite_vector(row):
            return (isinstance(row, list) and len(row) == 3
                    and all(type(value) in (int, float) and math.isfinite(value) for value in row))

        if ((intent is not None and any(not finite_vector(row) for row in intent))
                or any(not isinstance(row, list) or len(row) != 1 or type(row[0]) is not bool
                       for rows in (mask, sharp, automatic) if rows is not None for row in rows)
                or (encoded_type == "FLOAT_VECTOR" and encoded is not None
                    and any(not finite_vector(row) for row in encoded))
                or (encoded_type == "INT16_2D" and encoded is not None
                    and any(not isinstance(row, list) or len(row) != 2
                            or any(type(value) is not int or not -32768 <= value <= 32767 for value in row)
                            for row in encoded))):
            raise ValueError("Normal-storage attributes have malformed encoded or ownership rows.")
        witness = obj.get("normal_intent")
        if had_intent:
            if (not isinstance(witness, dict)
                    or any(witness.get(flag) is not True for flag in
                           ("encoded_matches", "normal_space_matches", "decoded_within_quantization"))
                    or witness.get("intent_normals") != intent):
                raise ValueError("Normal intent attributes differ from their authenticated encoding witness.")
        if had_mask:
            managed = [index for index, row in enumerate(mask) if row[0]]
            if (not had_intent or witness.get("unmanaged_matches") is not True
                    or witness.get("managed_loop_ids") != managed):
                raise ValueError("Managed normal attributes differ from their authenticated encoding witness.")
        if had_auto:
            if not had_intent or not had_mask or encoded_type != 'FLOAT_VECTOR' or automatic != mask:
                raise ValueError("Automatic normal attributes differ from their authenticated encoding witness.")
        return layers

    def atoms(obj, oid):
        """Each key identifies a stored value and its independently comparable region."""
        result = {}
        layers = attribute_layers(obj, oid)
        for key, value in {**obj, **layers}.items():
            if key in ignored or key == "has_custom_normals":
                continue
            if key in ("vertices_local", "vertices_world_mm", "corner_normals", "polygon_smooth", "polygon_material"):
                kind = ("vertex" if key.startswith("vertices_") else
                        "normal" if key == "corner_normals" else "polygon")
                if isinstance(value, list):
                    result[(key, "length")] = (whole, len(value))
                    result.update({(key, index): ((kind, index), row) for index, row in enumerate(value)})
                    continue
            if key == "uv_layers" and isinstance(value, list):
                result[(key, "layers")] = (whole, [{k: v for k, v in layer.items() if k != "uv"}
                                                    for layer in value])
                for layer_index, layer in enumerate(value):
                    rows = layer.get("uv", [])
                    result[(key, layer_index, "length")] = (whole, len(rows))
                    result.update({(key, layer_index, index): (("uv", layer_index, index), row)
                                   for index, row in enumerate(rows)})
                continue
            if key in ("normal_attributes", "attributes"):
                for name, layer in value.items():
                    result[(key, name, "metadata")] = (whole, {k: v for k, v in layer.items() if k != "values"})
                    rows = layer.get("values")
                    if not isinstance(rows, list):
                        result[(key, name)] = (whole, rows)
                        continue
                    domain = {"CORNER": "normal", "POINT": "vertex", "FACE": "polygon", "EDGE": "edge"}.get(layer.get("domain"))
                    result[(key, name, "length")] = (whole, len(rows))
                    result.update({(key, name, index): ((domain, index) if domain else whole, row)
                                   for index, row in enumerate(rows)})
                continue
            result[(key,)] = (whole, value)
        return result

    base_atoms = {oid: atoms(obj, oid) for oid, obj in source.items()}
    preview_atoms = [{oid: atoms(obj, oid) for oid, obj in objects.items()} for objects in previews]
    footprints = []
    missing = object()
    for pick, expected in zip(selected, preview_atoms):
        touched = {}
        for oid in source:
            a, b = base_atoms[oid], expected[oid]
            for key in set(a) | set(b):
                left, right = a.get(key), b.get(key)
                if left != right:
                    touched.setdefault(oid, set()).add((right or left)[0])
        for operation in pick.get("operations", []):
            scope = operation.get("scope", {})
            oid = scope.get("object_id")
            if oid not in source:
                raise ValueError("A selected operation does not identify its source object.")
            resources = touched.setdefault(oid, set())
            resources.update(("vertex", row["vertex_id"]) for row in scope.get("vertex_weights", []) if row["weight"])
            faces = set(scope.get("affected_polygon_ids", []))
            resources.update(("polygon", index) for index in faces)
            normal_faces = set(scope.get("normal_affected_polygon_ids", owner_mesh.normal_faces(source[oid], faces)))
            cursor = 0
            for index, polygon in enumerate(source[oid].get("polygons", [])):
                if index in normal_faces:
                    resources.update(("normal", loop) for loop in range(cursor, cursor + len(polygon)))
                cursor += len(polygon)
            for marking in operation.get("marking_attachments", []):
                marking_id = marking.get("object_id")
                if marking_id not in source:
                    raise ValueError("A selected marking does not identify its source object.")
                touched.setdefault(marking_id, set()).add(whole)
        footprints.append(touched)

    overlaps = []
    for first in range(len(selected)):
        for second in range(first + 1, len(selected)):
            objects = []
            for oid in set(footprints[first]) & set(footprints[second]):
                left, right = footprints[first][oid], footprints[second][oid]
                if whole in left or whole in right or left & right:
                    objects.append({"object_id": oid, "object": source[oid].get("name", oid)})
            if objects:
                overlaps.append({"item_ids": [selected[first]["item_id"], selected[second]["item_id"]],
                                 "objects": sorted(objects, key=lambda row: row["object_id"])})

    checked = [0 for _ in selected]
    for oid, obj in current.items():
        actual = atoms(obj, oid)
        expected_rows = [objects[oid] for objects in preview_atoms]
        for key in set(base_atoms[oid]) | set(actual) | set().union(*(set(rows) for rows in expected_rows)):
            record = actual.get(key) or base_atoms[oid].get(key) or next(rows[key] for rows in expected_rows if key in rows)
            resource = record[0]
            owners = [index for index, touched in enumerate(footprints)
                      if whole in touched.get(oid, ()) or resource in touched.get(oid, ())]
            if len(owners) > 1:
                continue
            wanted = expected_rows[owners[0]].get(key, missing) if owners else base_atoms[oid].get(key, missing)
            if actual.get(key, missing) != wanted:
                label = selected[owners[0]]["item_id"] if owners else "unrequested source"
                raise ValueError(f"Applied picks differ from {label}'s previewed result on {obj.get('name', oid)} ({key[0]}).")
            if owners:
                checked[owners[0]] += 1

    affected = {item for overlap in overlaps for item in overlap["item_ids"]}
    return {"ok": True, "mode": "single_glb_bytes" if len(selected) == 1 else "per_item_geometry",
            "single_pick_glb_equal": True if len(selected) == 1 else None,
            "comparison": "Exact stored per-vertex local/world positions, per-loop normals and UVs, polygon and material data; no tolerance. Topology and scalar changes use whole objects. Overlaps require separately verified item-order composition.",
            "items": [{"item_id": row["item_id"], "option_id": row["option_id"],
                       "exact_preview_geometry": row["item_id"] not in affected,
                       "exclusive_values_checked": checked[index],
                       "overlap_reported": row["item_id"] in affected}
                      for index, row in enumerate(selected)], "overlaps": overlaps}


def verify_choice_exports(source_export_path, selected, applied_export_path):
    """Independently compare the rendered GLB data, with exact accessor equality.

    Vertex sharing and accessor numbering may change during export; expanding
    triangle corners removes those storage differences. Node names and primitive
    order must remain unique and comparable. Shared changed layouts are refused.
    Material indices are resolved to content, including texture and sampler data.
    """
    import hashlib
    import numpy as np
    from ..g3d_host import glb

    if not isinstance(selected, list) or not selected:
        raise ValueError("Choose at least one sealed exported option.")

    def contents(path):
        asset = glb.load(path)
        if asset.scene_issue or asset.missing_uris or not asset.primitives:
            raise ValueError("The export cannot be compared as a complete scene.")
        names = {}
        for primitive in asset.primitives:
            name = asset.nodes[primitive.node_index].get("name")
            if (not isinstance(name, str) or not name or
                    (name in names and names[name] != primitive.node_index)):
                raise ValueError("Exported objects need unique exact node names for preview comparison.")
            names[name] = primitive.node_index

        def image_record(index):
            payload = asset.image_bytes(index)
            if payload is None:
                raise ValueError("A preview material texture is unavailable.")
            row = asset.doc.get("images", [])[index]
            return {"sha256": hashlib.sha256(payload).hexdigest(),
                    **{key: value for key, value in row.items() if key not in {"uri", "bufferView"}}}

        def texture_record(index):
            row = asset.doc.get("textures", [])[index]
            result = {}
            for key, value in row.items():
                if key == "source":
                    result[key] = image_record(value)
                elif key == "sampler":
                    result[key] = asset.doc.get("samplers", [])[value]
                elif key == "extensions":
                    result[key] = {extension: {field: image_record(item) if field == "source" else item
                                               for field, item in data.items()}
                                   for extension, data in value.items()}
                else:
                    result[key] = value
            return result

        def material_record(value, field=""):
            if isinstance(value, list):
                return [material_record(row, field) for row in value]
            if not isinstance(value, dict):
                return value
            return {key: (texture_record(item) if key == "index" and field.lower().endswith("texture")
                          else material_record(item, key)) for key, item in value.items()}

        records = {}
        for primitive in asset.primitives:
            if (primitive.error or primitive.index_issue or primitive.compressed or primitive.has_morph_targets
                    or primitive.mode != 4):
                raise ValueError("A preview contains geometry that cannot be compared exactly.")
            key = (primitive.node_name, primitive.primitive_index)
            if key in records:
                raise ValueError("A preview repeats an ambiguous primitive identity.")
            raw = asset.meshes[primitive.mesh_index]["primitives"][primitive.primitive_index]
            indices = primitive.triangles.reshape(-1)
            attributes = {}
            for semantic, accessor in raw["attributes"].items():
                values = asset.accessor(accessor)
                if (values.ndim != 2 or len(values) != len(primitive.positions) or not np.isfinite(values).all()):
                    raise ValueError("A preview attribute cannot be compared to its triangle corners.")
                attributes[semantic] = values[indices]
            records[key] = {"attributes": attributes, "world": primitive.world,
                            "material": material_record(asset.material(primitive.material_index)),
                            "primitive_state": {name: value for name, value in raw.items()
                                                if name not in {"attributes", "indices", "material", "mode"}}}
        return records

    def same(left, right):
        return (set(left["attributes"]) == set(right["attributes"])
                and all(np.array_equal(left["attributes"][name], right["attributes"][name])
                        for name in left["attributes"])
                and np.array_equal(left["world"], right["world"])
                and left["material"] == right["material"] and left["primitive_state"] == right["primitive_state"])

    source = contents(source_export_path)
    previews = [contents(row["export"]["path"]) for row in selected]
    applied = contents(applied_export_path)
    if set(applied) != set(source) or any(set(preview) != set(source) for preview in previews):
        raise ValueError("Applied and preview exports do not have the same object/primitive identities.")
    if len(selected) == 1 and edits.sha(applied_export_path) != edits.sha(selected[0]["export"]["path"]):
        raise ValueError("The single applied GLB bytes differ from the selected preview.")

    comparisons, overlap_rows = [], {}

    def overlaps(owners, key, field, count):
        for first_index, first in enumerate(owners):
            for second in owners[first_index + 1:]:
                pair = (first, second)
                row = overlap_rows.setdefault(pair, {"item_ids": [selected[first]["item_id"], selected[second]["item_id"]],
                                                     "objects": [], "attributes": {}})
                obj = {"object": key[0], "primitive_index": key[1]}
                if obj not in row["objects"]:
                    row["objects"].append(obj)
                row["attributes"][field] = row["attributes"].get(field, 0) + count

    for key, original in source.items():
        expected = [preview[key] for preview in previews]
        current = applied[key]
        changed = [index for index, row in enumerate(expected) if not same(original, row)]
        changed_layout = [index for index, row in enumerate(expected)
                          if set(row["attributes"]) != set(original["attributes"]) or any(
                              row["attributes"][name].shape != original["attributes"][name].shape
                              for name in set(row["attributes"]) & set(original["attributes"]))]
        if changed_layout:
            if len(changed) != 1 or not same(current, expected[changed[0]]):
                raise ValueError("Changed exported topology overlaps another pick or cannot reproduce its preview.")
            comparisons.append({"object": key[0], "primitive_index": key[1], "mode": "whole_primitive",
                                "item_id": selected[changed[0]]["item_id"]})
            continue
        if set(current["attributes"]) != set(original["attributes"]):
            raise ValueError("Applied export attributes differ from the previewed layout.")
        for field, baseline in original["attributes"].items():
            actual = current["attributes"][field]
            if actual.shape != baseline.shape:
                raise ValueError("Applied export triangle corners differ from the previewed layout.")
            masks = [np.any(row["attributes"][field] != baseline, axis=1) for row in expected]
            counts = np.sum(masks, axis=0)
            untouched = counts == 0
            if not np.array_equal(actual[untouched], baseline[untouched]):
                raise ValueError(f"Applied export changed unrequested {field} rows on {key[0]}.")
            for index, mask in enumerate(masks):
                exclusive = mask & (counts == 1)
                if not np.array_equal(actual[exclusive], expected[index]["attributes"][field][exclusive]):
                    raise ValueError(f"Applied export differs from {selected[index]['item_id']}'s previewed {field} on {key[0]}.")
            for first in range(len(selected)):
                for second in range(first + 1, len(selected)):
                    count = int(np.count_nonzero(masks[first] & masks[second]))
                    if count:
                        overlaps([first, second], key, field, count)
            comparisons.append({"object": key[0], "primitive_index": key[1], "attribute": field,
                                "triangle_corner_rows": len(baseline), "exclusive_rows": int(np.count_nonzero(counts == 1)),
                                "unchanged_rows": int(np.count_nonzero(untouched)), "overlapping_rows": int(np.count_nonzero(counts > 1))})
        for field in ("world", "material", "primitive_state"):
            equal = (lambda a, b: np.array_equal(a, b)) if field == "world" else (lambda a, b: a == b)
            owners = [index for index, row in enumerate(expected) if not equal(row[field], original[field])]
            if len(owners) > 1:
                overlaps(owners, key, field, 1)
            else:
                wanted = expected[owners[0]][field] if owners else original[field]
                if not equal(current[field], wanted):
                    raise ValueError(f"Applied export {field} differs from the shown result on {key[0]}.")
    return {"ok": True, "comparison": "Exact triangle-corner-expanded accessors for all attributes, node transforms and canonical material/texture content; no tolerance.",
            "primitives_checked": len(source), "comparisons": comparisons, "overlaps": list(overlap_rows.values()),
            "single_pick_glb_equal": True if len(selected) == 1 else None}
