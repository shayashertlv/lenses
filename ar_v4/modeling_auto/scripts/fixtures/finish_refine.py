"""Synthetic material-only refinement; each repeat produces fresh visible evidence."""
import bpy

for material in bpy.data.materials:
    if material.use_nodes:
        shader = material.node_tree.nodes.get('Principled BSDF')
        if shader:
            roughness = shader.inputs.get('Roughness')
            if roughness:
                roughness.default_value = max(.01, roughness.default_value * .65)
            coat = shader.inputs.get('Coat Weight')
            if coat and 'optical' not in material.name:
                coat.default_value = min(.8, coat.default_value + .12)
