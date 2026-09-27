"""Experimental Blender compiler for the LensAppearance v1 effective slab.

Runs inside Blender. It does not use physical ray displacement, a second lens
surface, or internal reflection. Shader math computes the canonical R and T;
an additive transparent/glossy closure realizes them under uniform incident
radiance. Roughness is passed through, but only zero-roughness uniform-light
conformance is currently claimed. This is not a production optical shader.
"""

from __future__ import annotations

import json
import math

from .lens_appearance import LensAppearance


def compile_lens_material(appearance: LensAppearance, name: str = "Canonical lens", *,
                          uv_layer: str = "LensUV"):
    """Create genuine shader math nodes; no baked lookup or CPU-colored emission."""
    if not isinstance(appearance, LensAppearance):
        raise TypeError("appearance must be LensAppearance")
    if appearance.rear_reflection_fraction_rgb is not None:
        raise ValueError('This experimental Blender compiler does not support canonical-axis rear response; use the AR renderer')
    import bpy
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material["lens_appearance_v1"] = json.dumps(appearance.to_dict(), sort_keys=True)
    material["compiler"] = "effective-slab-blender-v1"
    material["limitations"] = "one surface; no ray displacement; uniform illumination conformance; roughness 0"
    material.use_backface_culling = False
    nodes, links = material.node_tree.nodes, material.node_tree.links
    nodes.clear()

    def node(kind, label):
        value = nodes.new(kind)
        value.name = value.label = label
        return value

    def assign(socket, value):
        if isinstance(value, (int, float)):
            socket.default_value = float(value)
        else:
            links.new(value, socket)

    def op(kind, a, b=None, label=None):
        value = node("ShaderNodeMath", label or kind)
        value.operation = kind
        assign(value.inputs[0], a)
        if b is not None:
            assign(value.inputs[1], b)
        return value.outputs[0]

    def smoothstep(value, start, end):
        t = op("DIVIDE", op("SUBTRACT", value, start), end - start)
        t = op("MINIMUM", op("MAXIMUM", t, 0), 1)
        return op("MULTIPLY", op("MULTIPLY", t, t), op("SUBTRACT", 3, op("MULTIPLY", 2, t)))

    def interpolate(value, keys, position, rgb):
        result = list(getattr(keys[0], rgb))
        for previous, following in zip(keys, keys[1:]):
            weight = smoothstep(value, getattr(previous, position), getattr(following, position))
            result = [op("ADD", result[c], op("MULTIPLY", weight,
                       getattr(following, rgb)[c] - getattr(previous, rgb)[c])) for c in range(3)]
        return result

    def combine(channels, label):
        value = node("ShaderNodeCombineColor", label)
        value.mode = "RGB"
        for c, channel in enumerate(channels):
            assign(value.inputs[c], channel)
        return value.outputs[0]

    uv = node("ShaderNodeUVMap", "Lens-local coordinates")
    uv.uv_map = uv_layer
    separate = node("ShaderNodeSeparateXYZ", "Lens-local v")
    links.new(uv.outputs[0], separate.inputs[0])
    density = interpolate(separate.outputs["Y"], appearance.optical_density_keyframes,
                          "v", "optical_density_rgb")

    geometry = node("ShaderNodeNewGeometry", "Incident direction and surface normal")
    dot = node("ShaderNodeVectorMath", "Incidence cosine")
    dot.operation = "DOT_PRODUCT"
    links.new(geometry.outputs["Incoming"], dot.inputs[0])
    links.new(geometry.outputs["Normal"], dot.inputs[1])
    cosine = op("MINIMUM", op("ABSOLUTE", dot.outputs["Value"]), 1)
    if appearance.angular_reflectance_keyframes is None:
        fifth = op("POWER", op("SUBTRACT", 1, cosine), 5)
        reflection = [op("ADD", f0, op("MULTIPLY", 1 - f0, fifth))
                      for f0 in appearance.normal_reflectance_rgb]
    else:
        degrees = op("MULTIPLY", op("ARCCOSINE", cosine), 180 / math.pi)
        reflection = interpolate(degrees, appearance.angular_reflectance_keyframes,
                                 "angle_degrees", "reflectance_rgb")

    sin_squared = op("MAXIMUM", op("SUBTRACT", 1, op("MULTIPLY", cosine, cosine)), 0)
    internal_squared = op("SUBTRACT", 1, op("DIVIDE", sin_squared, appearance.refractive_index ** 2))
    internal_cosine = op("SQRT", op("MAXIMUM", internal_squared, 0))
    # At exact grazing with n=1 the zero-density limiting value is one, while
    # any positive density tends to zero. The floor only avoids 0/0 in nodes;
    # conformance poses are strictly below 90 degrees (a plane vanishes there).
    denominator = op("MAXIMUM", internal_cosine, 1e-12)
    transmission = [op("MULTIPLY", op("SUBTRACT", 1, reflection[c]),
                    op("EXPONENT", op("MULTIPLY", -1, op("DIVIDE", density[c], denominator))))
                    for c in range(3)]

    transparent = node("ShaderNodeBsdfTransparent", "Canonical transmission T")
    links.new(combine(transmission, "T RGB"), transparent.inputs["Color"])
    glossy = node("ShaderNodeBsdfGlossy", "Canonical reflection R")
    glossy.distribution = "GGX"
    glossy.inputs["Roughness"].default_value = appearance.roughness
    links.new(combine(reflection, "R RGB"), glossy.inputs["Color"])
    add = node("ShaderNodeAddShader", "T background + R environment")
    links.new(transparent.outputs[0], add.inputs[0])
    links.new(glossy.outputs[0], add.inputs[1])
    output = node("ShaderNodeOutputMaterial", "Effective slab output")
    links.new(add.outputs[0], output.inputs["Surface"])
    return material
