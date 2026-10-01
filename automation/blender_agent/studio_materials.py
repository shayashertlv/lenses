"""Bounded native-glTF material edits for Studio; geometry is never re-exported.

The HTTP service owns job/revision selection. This module receives trusted paths,
checks optimistic source identity, and writes a new file without overwriting one.
Colour controls are sRGB hex; glTF colour factors are linear RGB.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import struct
from typing import Any

MAX_GLB_BYTES = 128 * 1024 * 1024
JSON_CHUNK = 0x4E4F534A
COLOUR = re.compile(r"#[0-9a-fA-F]{6}\Z")

# None in the extension field means core metallic-roughness PBR.
CONTROL_FIELDS = {
    "base_color": (None, "baseColorFactor"),
    "metallic": (None, "metallicFactor"),
    "roughness": (None, "roughnessFactor"),
    "transmission": ("KHR_materials_transmission", "transmissionFactor"),
    "ior": ("KHR_materials_ior", "ior"),
    "clearcoat": ("KHR_materials_clearcoat", "clearcoatFactor"),
    "clearcoat_roughness": ("KHR_materials_clearcoat", "clearcoatRoughnessFactor"),
    "iridescence": ("KHR_materials_iridescence", "iridescenceFactor"),
    "iridescence_ior": ("KHR_materials_iridescence", "iridescenceIor"),
    "iridescence_thickness": ("KHR_materials_iridescence", "iridescenceThicknessMaximum"),
    "attenuation_color": ("KHR_materials_volume", "attenuationColor"),
    "attenuation_distance": ("KHR_materials_volume", "attenuationDistance"),
}


def _control(key: str, label: str, low=0, high=1, step=.01, **extra) -> dict:
    return {"key": key, "label": label, "type": "range", "min": low, "max": high,
            "step": step, "extension": CONTROL_FIELDS[key][0], **extra}


CONTROLS = [
    {"key": "base_color", "label": "Colour / tint", "type": "color", "extension": None,
     "help": "Tints the selected material and any existing texture. White adds no tint; darker colours darken it. This does not change transparency."},
    _control("metallic", "Mirror / metallic strength",
             help="0 gives a non-metal surface; 1 gives metal-like, coloured reflections. Higher values reduce diffuse colour and light passing through a lens."),
    _control("roughness", "Surface roughness",
             help="Low values make sharp, polished reflections. High values spread the highlights for a matte or frosted finish; they do not change the surface shape."),
    _control("transmission", "See-through strength", .001, 1, .001,
             help="Higher values let more background light pass through; lower values make it less see-through. Tint, metallic strength and absorption still affect visibility."),
    _control("ior", "Glass refraction", 1, 2.5, .01,
             help="1 is air-like; around 1.5 is glass-like. Changes reflections and refraction, not lens curvature. Crystal frames use straight look-through in AR, so they do not visibly bend the background."),
    _control("clearcoat", "Clearcoat gloss",
             help="0 removes the extra clear surface highlight; 1 gives the strongest coating. Use Clearcoat roughness to make that highlight sharp or soft."),
    _control("clearcoat_roughness", "Clearcoat roughness",
             help="Low values make the clear coating glossy; high values blur its reflections. Has no visible effect when Clearcoat gloss is 0."),
    _control("iridescence", "Colour-shift strength",
             help="0 removes the rainbow coating; 1 shows its full angle-dependent colour. Use Coating thickness to change the colours."),
    _control("iridescence_ior", "Coating refraction", 1, 3, .01,
             help="Changes how the thin coating shifts colour with viewing angle. Higher is not simply brighter. Has no effect when Colour-shift strength is 0."),
    _control("iridescence_thickness", "Coating thickness", 0, 1500, 1, unit="nm",
             help="Changes the rainbow hues in nanometres, not the physical lens thickness. Needs Colour-shift strength above 0; existing thickness textures also affect the result."),
    {"key": "attenuation_color", "label": "Absorption tint", "type": "color", "extension": "KHR_materials_volume",
     "help": "Colours light passing through the material's thickness. White adds no absorption tint. Requires a positive Absorption distance and an authored volume thickness."},
    _control("attenuation_distance", "Absorption distance", 0, 10, .001, unit="m",
             help="Smaller positive distances make absorption tint stronger; larger distances weaken it. 0 switches absorption off. Requires an authored volume thickness."),
]
CONTROL_BY_KEY = {item["key"]: item for item in CONTROLS}
DEFAULTS = {"base_color": [1, 1, 1, 1], "metallic": 1, "roughness": 1,
            "transmission": 0, "ior": 1.5, "clearcoat": 0, "clearcoat_roughness": 0,
            "iridescence": 0, "iridescence_ior": 1.3, "iridescence_thickness": 400,
            "attenuation_color": [1, 1, 1], "attenuation_distance": 0}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _linear(value: float) -> float:
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def _srgb(value: float) -> float:
    value = max(0, min(1, float(value)))
    return 12.92 * value if value <= .0031308 else 1.055 * value ** (1 / 2.4) - .055


def hex_to_linear(value: str) -> list[float]:
    if not isinstance(value, str) or COLOUR.fullmatch(value) is None:
        raise ValueError("Colour must be a six-digit hex value, for example #87c9b0")
    return [_linear(int(value[index:index + 2], 16) / 255) for index in (1, 3, 5)]


def linear_to_hex(value) -> str:
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError("Invalid material colour")
    return "#" + "".join(f"{round(_srgb(channel) * 255):02x}" for channel in value[:3])


def read_glb(path: Path) -> tuple[bytes, dict, bytes]:
    path = Path(path)
    if path.stat().st_size > MAX_GLB_BYTES:
        raise ValueError("Model exceeds the 128 MiB Studio limit")
    raw = path.read_bytes()
    if len(raw) < 20 or struct.unpack_from("<III", raw) != (0x46546C67, 2, len(raw)):
        raise ValueError("Expected a complete binary glTF 2.0 model")
    size, kind = struct.unpack_from("<II", raw, 12)
    if kind != JSON_CHUNK or size % 4 or 20 + size > len(raw):
        raise ValueError("Invalid GLB JSON chunk")
    doc = json.loads(raw[20:20 + size], parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON number")))
    if not isinstance(doc, dict) or not isinstance(doc.get("materials", []), list):
        raise ValueError("Invalid GLB document")
    # Assets must be self-contained. Never let a saved model initiate unrelated requests.
    for item in [*doc.get("buffers", []), *doc.get("images", [])]:
        if "uri" in item and not str(item["uri"]).startswith("data:"):
            raise ValueError("Studio requires a self-contained GLB")
    offset = 20 + size
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ValueError("Truncated GLB chunk")
        length, _ = struct.unpack_from("<II", raw, offset)
        if length % 4 or offset + 8 + length > len(raw):
            raise ValueError("Invalid GLB chunk length")
        offset += 8 + length
    return raw, doc, raw[20 + size:]


def _roles(doc: dict) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    meshes = doc.get("meshes", [])
    for node in doc.get("nodes", []):
        if "mesh" not in node:
            continue
        mesh = meshes[node["mesh"]]
        role = node.get("extras", {}).get("partRole") or mesh.get("extras", {}).get("partRole") or "detail"
        for primitive in mesh.get("primitives", []):
            if "material" in primitive:
                result.setdefault(primitive["material"], set()).add(str(role))
    return result


def _editable_keys(material: dict) -> list[str]:
    extensions = material.get("extensions", {})
    if "LENSES_lens_appearance" in extensions or "KHR_materials_unlit" in extensions:
        return []
    keys = ["base_color", "metallic", "roughness"]
    for key, (extension, field) in CONTROL_FIELDS.items():
        if extension and extension in extensions:
            # Crossing zero changes AR render membership; keep the live class stable.
            if key == "transmission" and extensions[extension].get(field, 0) <= 0:
                continue
            # Thickness maps encode varying coatings; a single thickness slider would be misleading.
            if key == "iridescence_thickness" and "iridescenceThicknessTexture" in extensions[extension]:
                continue
            keys.append(key)
    # Existing valid glTF values can exceed this editor's deliberately narrow
    # bounds. Leave those values alone instead of sending an invalid full-state
    # preview or silently clamping an authored material when the editor opens.
    bounded = []
    for key in keys:
        extension, field = CONTROL_FIELDS[key]
        container = extensions[extension] if extension else material.get("pbrMetallicRoughness", {})
        value = container.get(field, DEFAULTS[key])
        control = CONTROL_BY_KEY[key]
        if control["type"] == "color":
            valid = isinstance(value, list) and len(value) >= 3 and all(
                type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in value[:3])
        else:
            valid = type(value) in (int, float) and math.isfinite(value) and control["min"] <= value <= control["max"]
        if valid:
            bounded.append(key)
    return bounded


def validate_viewer(viewer: dict | None) -> dict:
    if viewer is None:
        return {"lens_reflection": 1.0}
    if not isinstance(viewer, dict) or set(viewer) - {"lens_reflection"}:
        raise ValueError("Unknown viewer setting")
    value = viewer.get("lens_reflection", 1)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not .3 <= value <= 4:
        raise ValueError("Lens reflection must be between 0.3 and 4")
    return {"lens_reflection": float(value)}


def describe_model(path: Path, *, revision: str = "original", viewer: dict | None = None) -> dict:
    raw, doc, _ = read_glb(path)
    roles = _roles(doc)
    canonical = "LENSES_lens_appearance" in doc.get("extensionsUsed", [])
    materials = []
    for index, material in enumerate(doc.get("materials", [])):
        keys = [] if canonical or index not in roles else _editable_keys(material)
        props = {}
        for key in keys:
            extension, field = CONTROL_FIELDS[key]
            container = material.get("extensions", {}).get(extension, {}) if extension else material.get("pbrMetallicRoughness", {})
            value = container.get(field, DEFAULTS[key])
            props[key] = linear_to_hex(value) if CONTROL_BY_KEY[key]["type"] == "color" else value
        materials.append({"id": str(index), "name": material.get("name") or f"Material {index + 1}",
                          "roles": sorted(roles.get(index, {"detail"})), "properties": props,
                          "editable_keys": keys, "editable": bool(keys),
                          "has_textures": any("Texture" in key for key in material.get("pbrMetallicRoughness", {})),
                          "note": ("This asset uses canonical optics; native material editing is unavailable." if canonical
                                   else "This material is not used by a scene mesh." if index not in roles else None)})
    return {"revision": revision, "model_sha256": sha256(raw), "materials": materials,
            "controls": deepcopy(CONTROLS), "viewer": validate_viewer(viewer),
            "source_blend_matches_revision": revision == "original"}


def _validated_value(key: str, value: Any):
    control = CONTROL_BY_KEY.get(key)
    if control is None:
        raise ValueError(f"Unknown material property: {key}")
    if control["type"] == "color":
        return hex_to_linear(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{control['label']} must be a finite number")
    if not control["min"] <= value <= control["max"]:
        raise ValueError(f"{control['label']} must be between {control['min']} and {control['max']}")
    return float(value)


def write_material_revision(source: Path, destination: Path, edits: dict, *, expected_sha256: str) -> dict:
    """Write only allowlisted material factors; retain every geometry/texture byte."""
    raw, doc, tail = read_glb(source)
    source_sha = sha256(raw)
    if source_sha != expected_sha256:
        raise ValueError("The model changed; reload it before saving this revision")
    if not isinstance(edits, dict) or len(edits) > len(doc.get("materials", [])):
        raise ValueError("Invalid material edits")
    if "LENSES_lens_appearance" in doc.get("extensionsUsed", []) and edits:
        raise ValueError("Native material controls cannot edit canonical optical descriptors")
    updated = deepcopy(doc)
    changed_fields = []
    for material_id, changes in edits.items():
        if not isinstance(material_id, str) or not re.fullmatch(r"0|[1-9][0-9]*", material_id):
            raise ValueError("Invalid material ID")
        index = int(material_id)
        if index >= len(doc.get("materials", [])) or not isinstance(changes, dict):
            raise ValueError("Invalid material edits")
        allowed = _editable_keys(doc["materials"][index])
        for key, requested in changes.items():
            if key not in allowed:
                raise ValueError(f"{key} cannot be edited on this material")
            value = _validated_value(key, requested)
            extension, field = CONTROL_FIELDS[key]
            original = doc["materials"][index]
            original_container = original.get("extensions", {}).get(extension, {}) if extension else original.get("pbrMetallicRoughness", {})
            baseline = original_container.get(field, DEFAULTS[key])
            # Hex controls round linear factors. Replaying their initial value must
            # preserve the exact original float, including omitted glTF defaults.
            if CONTROL_BY_KEY[key]["type"] == "color":
                if requested.lower() == linear_to_hex(baseline):
                    continue
            elif value == baseline:
                continue
            material = updated["materials"][index]
            container = material["extensions"][extension] if extension else material.setdefault("pbrMetallicRoughness", {})
            if key == "base_color":
                value.append(container.get(field, [1, 1, 1, 1])[3])
            if key == "attenuation_distance" and value == 0:
                container.pop(field, None)
            else:
                container[field] = value
            if key == "iridescence_thickness":
                # Without a texture glTF uses Maximum. Keep its legal interval ordered.
                container["iridescenceThicknessMinimum"] = min(container.get("iridescenceThicknessMinimum", 100), value)
            changed_fields.append(f"materials[{index}].{key}")
    untouched = deepcopy(updated)
    if "materials" in doc:
        untouched["materials"] = doc["materials"]
    if untouched != doc:
        raise AssertionError("A non-material field changed")
    if not changed_fields:
        result = raw
    else:
        payload = json.dumps(updated, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
        payload += b" " * (-len(payload) % 4)
        result = struct.pack("<III", 0x46546C67, 2, 20 + len(payload) + len(tail)) + struct.pack("<II", len(payload), JSON_CHUNK) + payload + tail
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(result)
    _, written, written_tail = read_glb(destination)
    if written != updated or written_tail != tail or Path(source).read_bytes() != raw:
        raise AssertionError("Material revision failed preservation checks")
    return {"source_sha256": source_sha, "model_sha256": sha256(result),
            "changed_fields": changed_fields, "binary_chunks_unchanged": True,
            "geometry_unchanged": True, "source_unchanged": True,
            "source_blend_matches_revision": not changed_fields}
