import bpy
for material in bpy.data.materials:
    if material.use_nodes:
        shader = material.node_tree.nodes.get('Principled BSDF')
        if shader:
            if 'optical' in material.name:
                shader.inputs['Roughness'].default_value = .045
                shader.inputs['Transmission Weight'].default_value = .96
                shader.inputs['IOR'].default_value = 1.46
            else:
                shader.inputs['Roughness'].default_value = .22
                shader.inputs['Coat Weight'].default_value = .18
