"""General owner appearance edits, append-only gradients and reusable looks.

Explicit model roles are the only editing authority. Every use of a material
must belong to one role; a shared frame/temple material is refused. Hardware
inherits an explicitly declared supporting role. Markings are not optical look
targets. Authored colour textures are retained and multiplied by solid tint;
gradients refuse those textures instead of silently replacing a photo print.

Copy rule: a single source lens appearance broadcasts to all target lens
materials. With multiple source lens materials, exact unique names must match.
Other roles copy only within the same job, by exact unique material name.
Automatic reapplication always uses exact role/name, with unmatched entries
retained in the reusable look and explained in its receipt.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import struct

from . import delivery, edits, owner_lens_edit as lens
from . import owner_look_gradient as gradient

SCHEMA = "g3d.owner_look/1"
ROLES = ("lens", "frame", "temple")
MARKING_STEMS = ("etch", "letter", "inscri", "decal", "logo", "mark", "signature", "laser")


def _marking(*values):
    from glasses3d.g3d_host.lens_id import tokens
    return any(token.startswith(MARKING_STEMS) for value in values
               for token in tokens(value.get("name", "") if isinstance(value, dict) else ""))


def _ownership(doc):
    active, by_mesh, uses = lens._active_nodes(doc), {}, {}
    for ni, node in enumerate(doc.get("nodes", [])):
        if "mesh" in node:
            by_mesh.setdefault(node["mesh"], []).append((node, ni in active))
    for mi, mesh in enumerate(doc.get("meshes", [])):
        for primitive in mesh.get("primitives", []):
            index = primitive.get("material")
            if type(index) is not int or not 0 <= index < len(doc["materials"]):
                continue
            material = doc["materials"][index]
            for node, visible in by_mesh.get(mi, [({}, False)]):
                roles = {lens._role(value) for value in (material, mesh, node)} - {None}
                if "hardware" in roles and roles.intersection({"frame", "temple"}):
                    roles.remove("hardware")
                uses.setdefault(index, []).append({"roles": roles,
                    "visible": visible and primitive.get("mode", 4) in (4, 5, 6),
                    "marking": _marking(material, mesh, node)})
    accepted, refused = {}, []
    for index, rows in sorted(uses.items()):
        if not any(row["visible"] for row in rows):
            continue
        roles = set.union(*(row["roles"] for row in rows))
        reason = None
        if len(roles) != 1 or next(iter(roles), None) not in ROLES or any(row["roles"] != roles for row in rows):
            reason = "This material is shared by different parts or has no unambiguous part role."
        elif "lens" in roles and any(row["marking"] for row in rows):
            reason = "Lens markings and decals keep their own appearance; they are not lens-look targets."
        if reason:
            refused.append({"material_index": index, "name": doc["materials"][index].get("name", f"Material {index + 1}"),
                            "roles": sorted(roles), "reason": reason})
        else:
            accepted[index] = {"role": next(iter(roles)), "copy_eligible": not any(row["marking"] for row in rows)}
    return accepted, refused


def _generated(material, doc, chunks):
    marker = material.get("extras", {})
    marker = marker.get(gradient.KEY) if isinstance(marker, dict) else None
    if marker is None:
        return None
    try:
        spec = gradient.validate_gradient(marker["spec"])
        if marker["schema"] != gradient.SCHEMA:
            raise ValueError()
        binary = next(body for kind, body, _ in chunks if kind == 0x004E4942)
        if type(marker["texcoord"]) is not int or not 0 <= marker["texcoord"] <= 3 or not marker["bindings"]:
            raise ValueError()
        for binding in marker["bindings"]:
            primitive = doc["meshes"][binding["mesh"]]["primitives"][binding["primitive"]]
            index = primitive["attributes"][f"TEXCOORD_{marker['texcoord']}"]
            if index != binding["accessor"]:
                raise ValueError()
            uv = gradient._accessor(doc, binary, index).astype("<f4").tobytes()
            position = gradient._accessor(doc, binary, primitive["attributes"]["POSITION"])
            if lens._sha(uv) != binding["uv_sha256"] or len(uv) != len(position) * 8:
                raise ValueError()
        if spec is not None:
            binding = material["pbrMetallicRoughness"]["baseColorTexture"]
            if binding != {"index": marker["texture"], "texCoord": marker["texcoord"]}:
                raise ValueError()
            texture = doc["textures"][marker["texture"]]
            image = doc["images"][texture["source"]]
            view = doc["bufferViews"][image["bufferView"]]
            raw = binary[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
            if (view["buffer"] != 0 or image.get("mimeType") != "image/png"
                    or lens._sha(raw) != marker["image_sha256"] or raw != gradient.image_bytes(spec)):
                raise ValueError()
        elif "baseColorTexture" in material.get("pbrMetallicRoughness", {}):
            raise ValueError()
        tint = marker["solid_tint"]
        if not isinstance(tint, list) or len(tint) != 3:
            raise ValueError()
        return {**marker, "spec": spec, "solid_tint": [lens._number(x, 0, 1, "Tint") for x in tint]}
    except (KeyError, TypeError, IndexError, StopIteration, ValueError) as exc:
        raise ValueError("This model's saved gradient metadata does not match its texture.") from exc


def _row_values(material, doc, chunks):
    result = lens._material_values(material)
    generated = _generated(material, doc, chunks)
    result["gradient"] = generated["spec"] if generated else None
    if generated:
        result["tint"] = generated["solid_tint"]
    return result


def _values(value):
    if not isinstance(value, dict) or set(value) - {"gradient"} != lens.FIELDS:
        raise ValueError("Provide every appearance control value, with no unknown fields.")
    return {**lens._values({key: value[key] for key in lens.FIELDS}),
            "gradient": gradient.validate_gradient(value.get("gradient"))}


def read_materials(source_glb):
    payload = lens._payload(source_glb)
    doc, chunks = lens._parse(payload)
    ownership, refused = _ownership(doc)
    rows = []
    for index, authority in ownership.items():
        material = doc["materials"][index]
        values = _row_values(material, doc, chunks)
        authored_texture = "baseColorTexture" in material.get("pbrMetallicRoughness", {}) and values["gradient"] is None
        rows.append({"material_index": index, "name": material.get("name", f"Material {index + 1}"),
            "material_name": material.get("name"), **authority, "values": values,
            "gradient": values["gradient"], "has_colour_texture": authored_texture,
            "can_gradient": authority["role"] == "lens" and not authored_texture,
            "can_transmission": authority["role"] == "lens" or
                "KHR_materials_transmission" in material.get("extensions", {}),
            "colour_note": "Colour tints the existing pattern." if authored_texture else "",
            "gradient_refusal": "This lens already has a colour texture. Solid colour tints it; gradient would replace it."
                if authored_texture and authority["role"] == "lens" else None})
    return {"schema": SCHEMA, "glb_sha256": lens._sha(payload), "materials": rows,
            "roles": [role for role in ROLES if any(row["role"] == role for row in rows)], "refusals": refused}


def _selection(source, values_by_material, *, allow_empty=False):
    metadata = read_materials(source)
    materials = {row["material_index"]: row for row in metadata["materials"]}
    if not isinstance(values_by_material, dict) or (not values_by_material and not allow_empty):
        raise ValueError("Choose at least one material and provide its appearance values.")
    values = {}
    for key, value in values_by_material.items():
        if type(key) is int:
            index = key
        elif isinstance(key, str) and re.fullmatch(r"0|[1-9][0-9]*", key):
            index = int(key)
        else:
            raise ValueError("Each part needs its exact material index.")
        if index not in materials or index in values:
            reason = next((row["reason"] for row in metadata["refusals"] if row["material_index"] == index),
                          "Only distinct materials belonging to one labelled part may be edited.")
            raise ValueError(reason)
        row, clean = materials[index], _values(value)
        if clean["gradient"] is not None and not row["can_gradient"]:
            raise ValueError(row["gradient_refusal"] or "Only lenses support gradient colour.")
        if clean["transmission"] != row["values"]["transmission"] and not row["can_transmission"]:
            raise ValueError("See-through is available for lenses and parts that already carry transmission.")
        values[index] = clean
    return materials, values


def patch_glb(source_glb, values_by_material, *, _allow_empty=False):
    original = lens._payload(source_glb)
    materials, values = _selection(original, values_by_material, allow_empty=_allow_empty)
    doc, chunks = lens._parse(original)
    after = copy.deepcopy(doc)
    changed = [i for i, value in values.items() if value != materials[i]["values"]]
    appender, projections = None, None
    bindings = []
    for index in changed:
        value, mat = values[index], after["materials"][index]
        before = materials[index]["values"]
        generated = _generated(doc["materials"][index], doc, chunks)
        pbr = mat.setdefault("pbrMetallicRoughness", {})
        pbr["baseColorFactor"] = [*value["tint"], pbr.get("baseColorFactor", [1, 1, 1, 1])[3]]
        pbr.update(metallicFactor=value["metallic"], roughnessFactor=value["roughness"])
        ext = mat.setdefault("extensions", {})
        if materials[index]["can_transmission"]:
            ext.setdefault(lens.EXTENSIONS[0], {})["transmissionFactor"] = value["transmission"]
        ext.setdefault(lens.EXTENSIONS[1], {})["ior"] = value["ior"]
        ext.setdefault(lens.EXTENSIONS[2], {}).update(iridescenceFactor=value["iridescence"],
            iridescenceIor=value["iridescence_ior"], iridescenceThicknessMinimum=value["iridescence_thickness_min"],
            iridescenceThicknessMaximum=value["iridescence_thickness_max"])
        if value["gradient"] is None:
            if generated:
                pbr.pop("baseColorTexture", None)
                mat["extras"][gradient.KEY].update(spec=None, texture=None, image_sha256=None,
                                                   solid_tint=value["tint"])
        else:
            pbr["baseColorFactor"][:3] = [1, 1, 1]
            if before["gradient"] != value["gradient"]:
                if appender is None:
                    appender = gradient.Appender(after, chunks)
                    projections = gradient.projection_data(doc, appender.original,
                        {i for i, row in materials.items() if row["role"] == "lens"})
                rows, xmin, xmax = projections
                targets = [row for row in rows if row["material"] == index]
                # First edit allocates a fresh semantic. Later edits rebind only
                # that verified owner semantic to a newly appended accessor,
                # preserving old byte ranges and avoiding Three's UV-set ceiling.
                existing = [int(key[9:]) for row in targets
                            for key in after["meshes"][row["mesh"]]["primitives"][row["primitive"]]["attributes"]
                            if re.fullmatch(r"TEXCOORD_[0-9]+", key)]
                texcoord = generated["texcoord"] if generated else next((i for i in range(4) if i not in existing), None)
                if texcoord is None:
                    raise ValueError("This lens already uses all four texture-coordinate sets supported by the viewer.")
                texture = appender.texture(value["gradient"])
                owner_bindings = []
                for row in targets:
                    uv = gradient.coordinates(row, value["gradient"]["direction"], xmin, xmax)
                    accessor = appender.uv(uv)
                    primitive = after["meshes"][row["mesh"]]["primitives"][row["primitive"]]
                    previous_accessor = primitive["attributes"].get(f"TEXCOORD_{texcoord}")
                    primitive["attributes"][f"TEXCOORD_{texcoord}"] = accessor
                    bindings.append({"material_index": index, "mesh_index": row["mesh"],
                        "primitive_index": row["primitive"], "attribute": f"TEXCOORD_{texcoord}",
                        "accessor": accessor, "previous_accessor": previous_accessor})
                    owner_bindings.append({"mesh": row["mesh"], "primitive": row["primitive"],
                                           "accessor": accessor, "uv_sha256": lens._sha(uv)})
                pbr["baseColorTexture"] = {"index": texture, "texCoord": texcoord}
                if not isinstance(mat.get("extras", {}), dict):
                    raise ValueError("This material's existing metadata cannot carry a saved gradient.")
                mat.setdefault("extras", {})[gradient.KEY] = {"schema": gradient.SCHEMA,
                    "spec": value["gradient"], "texture": texture, "texcoord": texcoord,
                    "image_sha256": lens._sha(gradient.image_bytes(value["gradient"])), "solid_tint": value["tint"],
                    "bindings": owner_bindings}
            else:
                mat["extras"][gradient.KEY]["solid_tint"] = value["tint"]
    if changed:
        used = after.setdefault("extensionsUsed", [])
        if not isinstance(used, list) or any(not isinstance(v, str) for v in used):
            raise ValueError("The model extension list is invalid.")
        declared = {name for i in changed for name in after["materials"][i].get("extensions", {})
                    if name in lens.EXTENSIONS}
        for name in lens.EXTENSIONS:
            if name not in declared:
                continue
            if name not in used:
                used.append(name)
        binary_chunks = appender.chunks_bytes() if appender else [raw for _, _, raw in chunks[1:]]
        encoded = json.dumps(after, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        encoded += b" " * (-len(encoded) % 4)
        body = struct.pack("<II", len(encoded), 0x4E4F534A) + encoded + b"".join(binary_chunks)
        patched = struct.pack("<4sII", b"glTF", 2, len(body) + 12) + body
    else:
        patched = original
    receipt = {"schema": "g3d.look_patch/1", "source_glb_sha256": lens._sha(original),
        "glb_sha256": lens._sha(patched), "values": {str(i): values[i] for i in sorted(values)},
        "changed_material_indices": sorted(changed), "binary_chunks_unchanged": appender is None,
        "existing_binary_ranges_unchanged": True, "unrelated_json_unchanged": True,
        "preserved_ranges": [{"chunk_type": kind, "byteOffset": 0, "byteLength": len(raw),
            "sha256": lens._sha(raw)} for kind, raw, _ in chunks[1:]],
        "added": copy.deepcopy(appender.added) if appender else [], "attribute_bindings_added": bindings,
        "extension_declarations_added": [name for name in after.get("extensionsUsed", [])
                                           if name not in doc.get("extensionsUsed", [])],
        "serialization": "Existing BIN byte ranges and unknown chunks stay exact. JSON changes only selected material values, gradient metadata/bindings, appended records, buffer length and extension declarations."}
    return patched, receipt


def build_look(source_glb, values_by_material=None):
    if values_by_material is None:
        values_by_material = {row["material_index"]: row["values"] for row in read_materials(source_glb)["materials"]
                              if row["copy_eligible"]}
    materials, values = _selection(source_glb, values_by_material, allow_empty=True)
    entries, skipped = [], []
    names = [(row["role"], row["material_name"]) for row in materials.values()]
    for index, value in sorted(values.items()):
        row = materials[index]
        if (not row["material_name"] or not row["copy_eligible"]
                or names.count((row["role"], row["material_name"])) != 1):
            skipped.append({"material_index": index,
                "reason": "An unnamed, ambiguously named material or marking cannot be a reusable look target."})
            continue
        entries.append({"role": row["role"], "material_name": row["material_name"], "values": value})
    return {"schema": SCHEMA, "entries": entries, "skipped": skipped}


def _look(value):
    if not isinstance(value, dict) or value.get("schema") != SCHEMA or not isinstance(value.get("entries"), list):
        raise ValueError("The reusable look is not valid.")
    result, seen = copy.deepcopy(value), set()
    for entry in result["entries"]:
        if (not isinstance(entry, dict) or set(entry) != {"role", "material_name", "values"}
                or entry["role"] not in ROLES or not isinstance(entry["material_name"], str) or not entry["material_name"]):
            raise ValueError("Each reusable look needs an exact role and material name.")
        key = (entry["role"], entry["material_name"])
        if key in seen:
            raise ValueError("The reusable look has ambiguous duplicate material names.")
        seen.add(key)
        entry["values"] = _values(entry["values"])
    return result


def map_look(look, target_glb, *, mode="reapply", same_job=True, role=None):
    if mode not in ("copy", "reapply") or (role is not None and role not in ROLES):
        raise ValueError("Choose a supported look mapping.")
    look = _look(look)
    targets = read_materials(target_glb)["materials"]
    entries = [entry for entry in look["entries"] if role is None or entry["role"] == role]
    lens_entries = [entry for entry in entries if entry["role"] == "lens"]
    result = {"values": {}, "applied": [], "skipped": []}
    for entry in entries:
        if mode == "copy" and entry["role"] != "lens" and not same_job:
            result["skipped"].append({**entry, "reason": "Frame and temple looks copy only between versions of the same job."})
            continue
        candidates = [row for row in targets if row["role"] == entry["role"] and row["copy_eligible"]
                      and (row["material_name"] == entry["material_name"]
                           or mode == "copy" and entry["role"] == "lens" and len(lens_entries) == 1)]
        broadcast = mode == "copy" and entry["role"] == "lens" and len(lens_entries) == 1
        if not candidates or (not broadcast and len(candidates) != 1):
            result["skipped"].append({**entry, "reason": "No unique material with the same role and name remains in this version."})
            continue
        for row in candidates:
            index = row["material_index"]
            try:
                _selection(target_glb, {index: entry["values"]})
                # Geometry/texture refusals are reported per match, never abort
                # otherwise safe entries or claim an unapplied gradient succeeded.
                if entry["values"]["gradient"] is not None:
                    patch_glb(target_glb, {index: entry["values"]})
            except ValueError as exc:
                result["skipped"].append({**entry, "material_index": index, "reason": str(exc)})
                continue
            result["values"][str(index)] = copy.deepcopy(entry["values"])
            result["applied"].append({"role": entry["role"], "source_material_name": entry["material_name"],
                                     "material_name": row["material_name"], "material_index": index})
    return result


def _application(loaded):
    binding = loaded["report_data"].get("owner_applied")
    if not binding:
        return None
    directory = Path(loaded["manifest"]).parent
    path = edits.confined(directory, binding["path"])
    payload = path.read_bytes()
    if lens._sha(payload) != binding["sha256"]:
        raise ValueError("This version's owner edit receipt changed.")
    return json.loads(payload)


def _binding(root, loaded):
    return {"root": str(Path(root).resolve()), "delivery_id": loaded["delivery_id"],
            "glb_sha256": loaded["glb_sha256"], "manifest_sha256": loaded["manifest_sha256"]}


def _verify_binding(binding):
    loaded = delivery.load_delivery(Path(binding["root"]), binding["delivery_id"])
    if any(loaded[key] != binding[key] for key in ("glb_sha256", "manifest_sha256")):
        raise ValueError("The native source version changed; the saved look cannot authorize it.")
    return loaded


def read_saved_look(source_root, delivery_id=None):
    loaded = delivery.load_delivery(source_root, delivery_id)
    application = _application(loaded)
    record = application.get("owner_look_edit") if application else None
    if record is None:
        if application and application.get("owner_lens_edit"):
            return _legacy_look(source_root, loaded)
        return None
    if record.get("schema") != "g3d.owner_look_edit/1" or record.get("glb_sha256") != loaded["glb_sha256"]:
        raise ValueError("This version's reusable look does not bind its model.")
    _look(record["look"])
    _verify_binding(record["native_source"])
    return copy.deepcopy(record)


def _legacy_look(source_root, loaded):
    """Migrate authenticated E2 receipts in memory; never rewrite old versions."""
    current, root, chain, seen, inherited = loaded, Path(source_root).resolve(), [], set(), None
    while True:
        identity = (str(root), current["delivery_id"])
        if identity in seen:
            raise ValueError("The appearance version chain contains a cycle.")
        seen.add(identity)
        application = _application(current)
        if application and application.get("owner_look_edit"):
            inherited = read_saved_look(root, current["delivery_id"])
            break
        old = application.get("owner_lens_edit") if application else None
        if old is None:
            break
        if old["patch"]["glb_sha256"] != current["glb_sha256"]:
            raise ValueError("The older lens edit receipt does not bind this version.")
        source = old["source"]
        binding = {"root": source["job"], **{key: source[key]
                    for key in ("delivery_id", "glb_sha256", "manifest_sha256")}}
        previous = _verify_binding(binding)
        chain.append((previous["model"], old["patch"]["values"]))
        current, root = previous, Path(binding["root"])
    entries = {(entry["role"], entry["material_name"]): entry
               for entry in (inherited or {}).get("look", {}).get("entries", [])}
    skipped = []
    for model, values in reversed(chain):
        for index, value in values.items():
            try:
                partial = build_look(model, {index: value})
            except ValueError as exc:
                skipped.append({"material_index": index, "reason": str(exc)})
                continue
            skipped.extend(partial["skipped"])
            for entry in partial["entries"]:
                entries[(entry["role"], entry["material_name"])] = entry
    native = inherited["native_source"] if inherited else _binding(root, current)
    return {"schema": "g3d.owner_look_edit/1", "look": {"schema": SCHEMA, "entries": list(entries.values()),
            "skipped": skipped}, "native_source": native, "glb_sha256": loaded["glb_sha256"],
            "source_glb_sha256": loaded["glb_sha256"], "mapping": {"migration": "legacy_lens_values"},
            "skipped": skipped}


def resolve_native_source(source_root, delivery_id=None):
    root, seen = Path(source_root).resolve(), set()
    while True:
        loaded = delivery.load_delivery(root, delivery_id)
        identity = (str(root), loaded["delivery_id"])
        if identity in seen:
            raise ValueError("The appearance version chain contains a cycle.")
        seen.add(identity)
        application = _application(loaded)
        if application and application.get("owner_look_edit"):
            binding = application["owner_look_edit"]["native_source"]
            _verify_binding(binding)
            return binding
        legacy = application.get("owner_lens_edit") if application else None
        if not legacy:
            return _binding(root, loaded)
        source = legacy["source"]
        binding = {"root": source["job"], **{key: source[key] for key in ("delivery_id", "glb_sha256", "manifest_sha256")}}
        _verify_binding(binding)
        root, delivery_id = Path(binding["root"]), binding["delivery_id"]


def create_preview(source_root, delivery_id, values_by_material, destination=None, *, open_ar=True):
    return lens.create_preview(source_root, delivery_id, values_by_material, destination,
                               open_ar=open_ar, _patcher=patch_glb)


def save_look_edit(source_root, delivery_id, values_by_material, destination=None, *, tool_test=False,
                   look=None, mapping=None):
    root, loaded, original = lens._source(source_root, delivery_id)
    inherited = read_saved_look(root, loaded["delivery_id"])
    if look is None:
        current = build_look(original, values_by_material)
        entries = {(entry["role"], entry["material_name"]): entry
                   for entry in (inherited or {}).get("look", {}).get("entries", [])}
        # The UI can send unchanged controls. Only actual owner changes become
        # durable overrides of Astra's future decisions.
        baseline = {row["material_index"]: row["values"] for row in read_materials(original)["materials"]}
        changed = {int(key): value for key, value in values_by_material.items()
                   if _values(value) != baseline.get(int(key))}
        for entry in build_look(original, changed)["entries"]:
            entries[(entry["role"], entry["material_name"])] = entry
        look = {"schema": SCHEMA, "entries": list(entries.values()), "skipped": current["skipped"]}
    look = _look(look)
    patched, receipt = patch_glb(original, values_by_material, _allow_empty=True)
    record = {"schema": "g3d.owner_look_edit/1", "look": look,
              "native_source": resolve_native_source(root, loaded["delivery_id"]),
              "glb_sha256": receipt["glb_sha256"], "source_glb_sha256": loaded["glb_sha256"],
              "mapping": copy.deepcopy(mapping), "skipped": copy.deepcopy((mapping or {}).get("skipped", []))}
    def bound_patch(source, values):
        if lens._sha(lens._payload(source)) != loaded["glb_sha256"]:
            raise ValueError("The source changed before its look could be saved.")
        return patched, receipt
    return lens.save_lens_edit(root, loaded["delivery_id"], values_by_material, destination,
                              tool_test=tool_test, _patcher=bound_patch, _look_record=record)


read_look_materials = read_materials
patch_look_glb = patch_glb
create_look_preview = create_preview
