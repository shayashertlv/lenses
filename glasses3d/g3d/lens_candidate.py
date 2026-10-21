"""Scalar lens authoring with exact glTF values and a live-node staleness guard."""
import json
import math

import bpy

from .coating import _active_outputs, _controls, lens_coating

KEY = "g3d_lens_candidate"
FIELDS = {"tint", "transmission", "metallic", "roughness", "ior", "iridescence", "iridescence_ior",
          "iridescence_thickness_min", "iridescence_thickness_max"}
SOCKETS = ("Base Color", "Transmission Weight", "Metallic", "Roughness", "IOR", "Thin Film IOR", "Thin Film Thickness")


def _shader(material):
    outputs = _active_outputs(material.node_tree)
    if len(outputs) != 1:
        raise ValueError("apply_lens_candidate requires exactly one active material output")
    source = outputs[0].inputs["Surface"]
    while source.is_linked and source.links[0].from_node.type == "REROUTE":
        source = source.links[0].from_node.inputs[0]
    shader = source.links[0].from_node if source.is_linked else None
    if shader is None or shader.type != "BSDF_PRINCIPLED" or shader.mute:
        raise ValueError("apply_lens_candidate requires a directly connected Principled shader")
    if any(name not in shader.inputs or shader.inputs[name].is_linked for name in SOCKETS):
        raise ValueError("apply_lens_candidate requires unlinked scalar lens inputs; linked textures stay authored")
    return shader


def _state(material):
    shader = _shader(material)
    controls = list(_controls(material.node_tree))
    if len(controls) != 1 or controls[0][1]:
        raise ValueError("apply_lens_candidate requires one top-level glTF control group")
    control = controls[0][0]
    values = {}
    for name in SOCKETS:
        value = shader.inputs[name].default_value
        values[name] = list(value) if name == "Base Color" else float(value)
    for name in ("Iridescence Factor", "Iridescence Thickness Minimum"):
        socket = control.inputs.get(name)
        if socket is None or socket.is_linked:
            raise ValueError("apply_lens_candidate export controls changed")
        values[name] = float(socket.default_value)
    return values


def _values(values):
    if set(values) != FIELDS or not isinstance(values["tint"], (list, tuple)) or len(values["tint"]) != 3:
        raise ValueError("Pass all nine candidate fields, with three linear RGB tint values and no label")
    values = {**values, "tint": list(values["tint"])}
    for name, value in values.items():
        for v in value if name == "tint" else [value]:
            if type(v) not in (int, float) or not math.isfinite(v):
                raise ValueError("Lens candidate values must be finite numbers")
            lower = 1 if name == "iridescence_ior" else 0
            if v < lower or (name in {"tint", "transmission", "metallic", "roughness", "iridescence"} and v > 1):
                raise ValueError("Lens candidate value is outside the glTF range")
    if 0 < values["ior"] < 1:
        raise ValueError("Lens IOR is zero or at least one")
    return values


def apply_lens_candidate(material, *, source_double_sided, **values):
    """Apply every scalar from compare_lens_candidates (omit label), preserving other material settings.

    Native Principled/control sockets mirror the values within Blender's supported ranges. Exact glTF
    scalars, including inactive zero-factor thickness and IOR=0, are retained in guarded export metadata.
    source_double_sided is the candidate tool's exported-material flag; preserve it when transmission
    crosses zero. The exporter's closed-outward safety rule still applies. Native thin film has no strength
    socket: judge the exported production AR result. A changed controlled socket/link state invalidates
    that metadata; the exporter then follows the live graph.
    Textured/linked inputs refuse before mutation. No material, geometry, camera or checkpoint is selected.
    """
    values = _values(values)
    if type(source_double_sided) is not bool:
        raise ValueError("source_double_sided must be the candidate's exported boolean")
    if isinstance(material, str):
        material = bpy.data.materials.get(material)
    if (not isinstance(material, bpy.types.Material) or not material.use_nodes or material.node_tree is None
            or material.library or material.is_evaluated):
        raise ValueError("apply_lens_candidate needs an original local node material")
    shader = _shader(material)
    # lens_coating performs all graph/control preflight before its first mutation.
    film_ior = shader.inputs["Thin Film IOR"].bl_rna.properties["default_value"]
    thickness = shader.inputs["Thin Film Thickness"].bl_rna.properties["default_value"]
    native_ior = min(film_ior.hard_max, max(film_ior.hard_min, values["iridescence_ior"]))
    native_max = min(thickness.hard_max, values["iridescence_thickness_max"])
    lens_coating(material, factor=values["iridescence"], ior=native_ior,
        thickness_min_nm=min(values["iridescence_thickness_min"], native_max),
        thickness_max_nm=native_max, tint=values["tint"])
    for socket, name in (("Transmission Weight", "transmission"), ("Metallic", "metallic"),
                         ("Roughness", "roughness"), ("IOR", "ior")):
        shader.inputs[socket].default_value = values[name]
    material.use_backface_culling = not source_double_sided
    material[KEY] = json.dumps({"values": values, "state": _state(material)}, sort_keys=True, allow_nan=False)
    return {"status": "configured", "material": material.name, "values": values,
            "exact_glTF_export": True, "native_preview": "Blender socket ranges apply; judge production AR."}


def exact_values(material):
    """Return only metadata still bound to the current controlled nodes; never restore stale choices."""
    raw = material.get(KEY)
    if not isinstance(raw, str):
        return None
    try:
        saved = json.loads(raw)
        return _values(saved["values"]) if saved["state"] == _state(material) else None
    except (ValueError, KeyError, TypeError):
        return None


def patch_material(material, values):
    pbr = material.setdefault("pbrMetallicRoughness", {})
    pbr["baseColorFactor"] = [*values["tint"], pbr.get("baseColorFactor", [1, 1, 1, 1])[3]]
    pbr.update(metallicFactor=values["metallic"], roughnessFactor=values["roughness"])
    ext = material.setdefault("extensions", {})
    ext.setdefault("KHR_materials_transmission", {})["transmissionFactor"] = values["transmission"]
    ext.setdefault("KHR_materials_ior", {})["ior"] = values["ior"]
    ext.setdefault("KHR_materials_iridescence", {}).update(iridescenceFactor=values["iridescence"],
        iridescenceIor=values["iridescence_ior"], iridescenceThicknessMinimum=values["iridescence_thickness_min"],
        iridescenceThicknessMaximum=values["iridescence_thickness_max"])
