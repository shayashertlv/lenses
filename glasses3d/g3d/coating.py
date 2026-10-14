"""In-memory lens coating authoring and post-export optical diagnostics (Blender only).

The glTF control group is deliberately unconnected: Blender reads its named inputs as export metadata.
All other optical authoring is read only along the active material output graph.
"""
from __future__ import annotations

import math

import bpy

GROUP_PREFIXES = ("gltf material output", "gltf settings")
CONTROL_NAMES = ("Iridescence Factor", "Iridescence Thickness Minimum", "Occlusion")
COATING_KEY = "g3d_lens_coating"


def _active_outputs(tree):
    return [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output and not n.mute]


def _group_output(tree):
    return next((n for n in tree.nodes if n.type == "GROUP_OUTPUT" and n.is_active_output), None)


def _matching(sockets, socket):
    return next((s for s in sockets if s.identifier == socket.identifier), None)


def _constant(socket, stack=(), seen=None):
    """Resolve constants and reroutes, including group instance inputs; unknown graphs stay unknown."""
    if socket is None:
        return None
    seen = set() if seen is None else seen
    key = (socket.as_pointer(), tuple(n.as_pointer() for n in stack))
    if key in seen:
        return None
    seen.add(key)
    if not socket.is_linked:
        value = getattr(socket, "default_value", None)
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return tuple(float(v) for v in value)
        except TypeError:
            return None
    link = socket.links[0]
    node = link.from_node
    if node.type in {"VALUE", "RGB"}:
        value = link.from_socket.default_value
        return float(value) if isinstance(value, (int, float)) else tuple(value)
    if node.type == "REROUTE":
        return _constant(node.inputs[0], stack, seen)
    if node.type == "GROUP_INPUT" and stack:
        return _constant(_matching(stack[-1].inputs, link.from_socket), stack[:-1], seen)
    if node.type == "GROUP" and node.node_tree and not node.mute:
        output = _group_output(node.node_tree)
        return _constant(_matching(output.inputs, link.from_socket), (*stack, node), seen) if output else None
    return None


def _upstream(socket, stack=(), seen=None):
    """Only contributing shader branches, with conservative traversal for nonconstant mixes."""
    seen = set() if seen is None else seen
    if socket is None:
        return
    for link in socket.links:
        node, output = link.from_node, link.from_socket
        key = (output.as_pointer(), tuple(n.as_pointer() for n in stack))
        if key in seen:
            continue
        seen.add(key)
        if node.mute:
            for internal in node.internal_links:
                if internal.to_socket == output:
                    yield from _upstream(internal.from_socket, stack, seen)
            continue
        if node.type == "GROUP" and node.node_tree:
            group_output = _group_output(node.node_tree)
            if group_output:
                yield from _upstream(_matching(group_output.inputs, output), (*stack, node), seen)
            continue
        if node.type == "GROUP_INPUT" and stack:
            yield from _upstream(_matching(stack[-1].inputs, output), stack[:-1], seen)
            continue
        yield node, stack
        inputs = node.inputs
        if node.type == "MIX_SHADER":
            factor = _constant(node.inputs[0], stack)
            inputs = [node.inputs[1]] if factor == 0 else [node.inputs[2]] if factor == 1 else node.inputs[1:]
        for inp in inputs:
            yield from _upstream(inp, stack, seen)


def _controls(tree, stack=()):
    for node in tree.nodes:
        if node.type != "GROUP" or node.node_tree is None or node.mute:
            continue
        if node.node_tree.name.lower().startswith(GROUP_PREFIXES):
            yield node, stack
        # Group nesting is acyclic in Blender; each instance's inputs can differ.
        yield from _controls(node.node_tree, (*stack, node))


def _scalar(socket, stack=()):
    value = _constant(socket, stack)
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _colour(socket, stack=()):
    value = _constant(socket, stack)
    return value if isinstance(value, tuple) and len(value) >= 3 else None


def lens_coating(material, *, factor, ior, thickness_min_nm, thickness_max_nm, tint):
    """Author a uniform exportable thin film on an existing directly connected Principled material.

    ``tint`` is three linear RGB components. Existing alpha, transmission, bulk IOR and unrelated nodes stay intact.
    Without a thickness texture glTF uses only maximum thickness; minimum is retained as an inactive control.
    Blender has no thin-film strength socket: partial factor is a glTF/AR control, not an exact Blender preview.
    Refuses linked/ambiguous controlled inputs before changing anything; never replaces a shader graph.
    """
    if isinstance(material, str):
        material = bpy.data.materials.get(material)
    if (not isinstance(material, bpy.types.Material) or not material.use_nodes or material.node_tree is None
            or material.library or material.is_evaluated):
        raise ValueError("lens_coating needs an original local material with an active node tree")
    values = {}
    for name, value in (("factor", factor), ("ior", ior), ("thickness_min_nm", thickness_min_nm),
                        ("thickness_max_nm", thickness_max_nm)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number")
        values[name] = float(value)
    factor, ior, minimum, maximum = (values[k] for k in values)
    if not 0 <= factor <= 1 or ior < 1 or not 0 <= minimum <= maximum:
        raise ValueError("factor must be 0..1, film IOR >= 1, and 0 <= minimum <= maximum thickness in nm")
    try:
        colour = tuple(tint)
    except TypeError as exc:
        raise ValueError("tint must be three finite linear RGB values in 0..1") from exc
    if len(colour) != 3 or any(isinstance(v, bool) or not isinstance(v, (int, float))
                               or not math.isfinite(v) or not 0 <= v <= 1 for v in colour):
        raise ValueError("tint must be three finite linear RGB values in 0..1")
    tree = material.node_tree
    outputs = _active_outputs(tree)
    if len(outputs) != 1:
        raise ValueError("lens_coating needs exactly one active material output")
    source = outputs[0].inputs["Surface"]
    while source.is_linked and source.links[0].from_node.type == "REROUTE":
        source = source.links[0].from_node.inputs[0]
    principled = source.links[0].from_node if source.is_linked else None
    if principled is None or principled.type != "BSDF_PRINCIPLED" or principled.mute:
        raise ValueError("lens_coating needs a Principled BSDF directly feeding the active Surface (reroutes allowed)")
    for name in ("Base Color", "Thin Film Thickness", "Thin Film IOR"):
        if name not in principled.inputs or principled.inputs[name].is_linked:
            raise ValueError(f"lens_coating cannot replace the linked or missing Principled {name} input")
    for name, value in (("Thin Film Thickness", maximum), ("Thin Film IOR", ior)):
        prop = principled.inputs[name].bl_rna.properties["default_value"]
        if not prop.hard_min <= value <= prop.hard_max:
            raise ValueError(f"{name} is outside Blender's supported range {prop.hard_min}..{prop.hard_max}")
    controls = list(_controls(tree))
    if len(controls) > 1 or any(stack for _, stack in controls):
        raise ValueError("lens_coating needs at most one top-level glTF control group; existing groups are preserved")
    control = controls[0][0] if controls else None
    # Blender selects the first named control socket. Refuse competing controls rather than deleting nodes.
    def competing(nt, own):
        for n in nt.nodes:
            if n.type == "GROUP" and n.node_tree is not None and not n.mute:
                if n != own and any(name in n.inputs for name in CONTROL_NAMES):
                    return True
                if competing(n.node_tree, None):
                    return True
        return False
    if competing(tree, control):
        raise ValueError("lens_coating found competing export-control sockets in another group")
    if control:
        for name in CONTROL_NAMES[:2]:
            if name in control.inputs and (control.inputs[name].is_linked or control.inputs[name].type != "VALUE"):
                raise ValueError(f"lens_coating cannot replace linked or non-float {name}")
            if name in control.inputs:
                value = factor if name == "Iridescence Factor" else minimum
                # Interface ranges can be tighter than the RNA socket's generic float range.
                interface = next((s for s in control.node_tree.interface.items_tree
                                  if s.item_type == "SOCKET" and s.in_out == "INPUT"
                                  and s.identifier == control.inputs[name].identifier), None)
                if interface is not None and not interface.min_value <= value <= interface.max_value:
                    raise ValueError(f"{name} is outside the existing group's supported range "
                                     f"{interface.min_value}..{interface.max_value}")
        if "Occlusion" in control.inputs and control.inputs["Occlusion"].type != "VALUE":
            raise ValueError("the existing Occlusion export socket must be a float")
    # All refusals above are read-only. Interface edits use a private group copy when other instances share it.
    if control is None:
        group = bpy.data.node_groups.new("glTF Material Output | lens coating", "ShaderNodeTree")
        control = tree.nodes.new("ShaderNodeGroup")
        control.node_tree = group
    elif any(name not in control.inputs for name in CONTROL_NAMES) and (control.node_tree.users > 1 or control.node_tree.library):
        defaults = {s.identifier: (tuple(s.default_value) if s.type in {"VECTOR", "RGBA"} else s.default_value)
                    for s in control.inputs if hasattr(s, "default_value")}
        control.node_tree = control.node_tree.copy()
        for socket in control.inputs:
            value = defaults.get(socket.identifier)
            if value is not None and hasattr(socket, "default_value"):
                socket.default_value = value
    group = control.node_tree
    for name, default in (("Occlusion", 1.0), ("Iridescence Factor", 0.0), ("Iridescence Thickness Minimum", 100.0)):
        if name not in control.inputs:
            group.interface.new_socket(name=name, in_out="INPUT", socket_type="NodeSocketFloat").default_value = default
            control.inputs[name].default_value = default
    control.inputs["Iridescence Factor"].default_value = factor
    control.inputs["Iridescence Thickness Minimum"].default_value = minimum
    effective = maximum if factor > 0 else 0.0
    principled.inputs["Thin Film Thickness"].default_value = effective
    principled.inputs["Thin Film IOR"].default_value = ior
    alpha = principled.inputs["Base Color"].default_value[3]
    principled.inputs["Base Color"].default_value = (*colour, alpha)
    material[COATING_KEY] = 1
    return {"status": "configured", "material": material.name, "principled": principled.name,
            "group": group.name, **values, "tint": list(colour), "tint_space": "linear RGB",
            "effective_uniform_thickness_nm": effective, "thickness_texture": False,
            "notes": ["Without a thickness texture, glTF uses uniform maximum thickness; minimum is inactive.",
                      "Partial factor controls glTF/AR; Blender's native thin film has no matching strength input."]}


def coating_range(material):
    """Read current helper-owned scalar controls for the trusted GLB range repair, never stale metadata values."""
    if material.get(COATING_KEY) != 1 or material.node_tree is None:
        return None
    tree = material.node_tree
    controls = list(_controls(tree))
    outputs = _active_outputs(tree)
    if len(controls) != 1 or controls[0][1] or len(outputs) != 1:
        return None
    control = controls[0][0]
    source = outputs[0].inputs["Surface"]
    while source.is_linked and source.links[0].from_node.type == "REROUTE":
        source = source.links[0].from_node.inputs[0]
    p = source.links[0].from_node if source.is_linked else None
    if p is None or p.type != "BSDF_PRINCIPLED" or p.mute:
        return None
    sockets = [control.inputs.get("Iridescence Factor"), control.inputs.get("Iridescence Thickness Minimum"),
               p.inputs.get("Thin Film Thickness")]
    if any(s is None or s.is_linked for s in sockets):
        return None
    factor, minimum, maximum = [_scalar(s) for s in sockets]
    if None in (factor, minimum, maximum) or not (0 < factor <= 1 and 0 <= minimum <= maximum and maximum > 0):
        return None
    return {"iridescenceThicknessMinimum": minimum, "iridescenceThicknessMaximum": maximum}


def optical_intent(material):
    """Describe contributing optical controls, without changing or evaluating arbitrary shader graphs."""
    tree = material.node_tree
    if tree is None or not material.use_nodes:
        return []
    controls = list(_controls(tree))
    def control(name):
        return next((_scalar(n.inputs[name], stack) for n, stack in controls if name in n.inputs), None)
    def has_control(name):
        return any(name in n.inputs for n, _ in controls)
    authored = []
    def add(effect, extension, field, expected, default, *, linked=False):
        authored.append({"effect": effect, "extension": extension, "field": field,
                         "expected": expected, "default": default, "linked": linked})
    seen = set()
    for output in _active_outputs(tree):
        for socket in (output.inputs["Surface"], output.inputs["Volume"]):
            for node, stack in _upstream(socket):
                key = (node.as_pointer(), tuple(n.as_pointer() for n in stack))
                if key in seen:
                    continue
                seen.add(key)
                if node.type == "BSDF_PRINCIPLED":
                    if "Weight" in node.inputs and _constant(node.inputs["Weight"], stack) == 0:
                        continue
                    def value(name):
                        return _scalar(node.inputs.get(name), stack)
                    for name, effect, ext, field in (
                        ("Transmission Weight", "transmission", "transmission", "transmissionFactor"),
                        ("Coat Weight", "coat", "clearcoat", "clearcoatFactor"),
                        ("Anisotropic", "anisotropy", "anisotropy", "anisotropyStrength")):
                        v = value(name)
                        if v is None or v > 0:
                            add(effect, "KHR_materials_" + ext, field, v, 0.0, linked=v is None)
                    thickness, film_factor = value("Thin Film Thickness"), control("Iridescence Factor")
                    if (thickness is None or thickness > 0) and film_factor != 0:
                        add("thin film", "KHR_materials_iridescence", "iridescenceThicknessMaximum", thickness, 400.0,
                            linked=thickness is None)
                        film_ior = value("Thin Film IOR")
                        add("film IOR", "KHR_materials_iridescence", "iridescenceIor", film_ior, 1.3,
                            linked=film_ior is None)
                        if film_factor is not None:
                            add("film strength", "KHR_materials_iridescence", "iridescenceFactor", film_factor, 0.0)
                    ior_value = value("IOR")
                    if ior_value is None or abs(ior_value - 1.5) > 1e-5:
                        add("IOR", "KHR_materials_ior", "ior", ior_value, 1.5, linked=node.inputs["IOR"].is_linked)
                    sheen = value("Sheen Weight")
                    sheen_tint = _colour(node.inputs.get("Sheen Tint"), stack)
                    if (sheen is None or sheen > 0) and (sheen_tint is None or any(sheen_tint[:3])):
                        add("sheen", "KHR_materials_sheen", None, sheen, 0.0, linked=sheen is None)
                    specular = value("Specular IOR Level")
                    if specular is not None and specular < .5:
                        add("specular", "KHR_materials_specular", "specularFactor", specular * 2, 1.0)
                    elif specular is None:
                        add("specular", "KHR_materials_specular", None, None, None, linked=True)
                    coat = value("Coat Weight")
                    if coat is None or coat > 0:
                        coat_ior = value("Coat IOR")
                        if coat_ior is None or abs(coat_ior - 1.5) > 1e-5:
                            add("coat IOR", None, None, coat_ior, 1.5, linked=coat_ior is None)
                        coat_tint = _colour(node.inputs.get("Coat Tint"), stack)
                        if coat_tint is None or any(abs(v - 1) > 1e-5 for v in coat_tint[:3]):
                            add("coat tint", None, None, coat_tint, (1., 1., 1., 1.), linked=coat_tint is None)
                    subsurface = value("Subsurface Weight")
                    if subsurface is None or subsurface > 0:
                        add("subsurface scattering", None, None, subsurface, 0.0, linked=subsurface is None)
                elif node.type == "VOLUME_ABSORPTION":
                    density = _scalar(node.inputs["Density"], stack)
                    if density is None or density > 0:
                        add("volume absorption", "KHR_materials_volume", None, density, 0.0, linked=density is None)
                elif node.type in {"BSDF_GLASS", "BSDF_REFRACTION", "VOLUME_SCATTER", "PRINCIPLED_VOLUME"}:
                    add(node.type.lower().replace("_", " "), None, None, None, None, linked=True)
    thickness = control("Thickness")
    if has_control("Thickness") and (thickness is None or thickness > 0):
        add("volume", "KHR_materials_volume", "thicknessFactor", thickness, 0.0, linked=thickness is None)
    dispersion = control("Dispersion")
    if has_control("Dispersion") and (dispersion is None or dispersion > 0):
        add("dispersion", "KHR_materials_dispersion", "dispersion", dispersion, 0.0, linked=dispersion is None)
    return authored


def optical_warnings(authored, exported, material_name):
    """Warnings against this material's final JSON, honoring omitted glTF defaults (no acceptance gate)."""
    extensions = exported.get("extensions") or {}
    warnings = []
    for item in authored:
        ext, expected = item["extension"], item["expected"]
        present = ext in extensions if ext else False
        actual = (extensions.get(ext) or {}).get(item["field"], item["default"]) if item["field"] else None
        # Unsupported shader graphs are explicitly unverified; their inactive/unlinked counterparts were excluded.
        if item["linked"]:
            warnings.append(f"material {material_name!r}: active {item['effect']} uses linked/unsupported shader wiring; "
                            f"its optical result cannot be verified from the exported GLB ({ext or 'no direct glTF mapping'})")
        elif not present or (item["field"] and expected is not None
                and (not isinstance(actual, (int, float)) or abs(actual - expected) > 1e-4 * max(1, abs(expected)))):
            warnings.append(f"material {material_name!r}: authored {item['effect']} {expected!r} was dropped or changed; "
                            f"exported {ext or 'no glTF extension'}.{item['field'] or '*'} = {actual!r}")
    return list(dict.fromkeys(warnings))
