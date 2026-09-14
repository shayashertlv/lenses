import bpy

# Exact face identification for this authored fixture only; not a generic
# product segmentation algorithm. The original optical caps share rim vertices.
transparent = bpy.data.materials.new('source optical transparency')
transparent.use_nodes = True
transparent.surface_render_method = 'DITHERED'
shader = transparent.node_tree.nodes.get('Principled BSDF')
shader.inputs['Alpha'].default_value = 0
shader.inputs['Roughness'].default_value = 1
shader.inputs['Specular IOR Level'].default_value = 0
for obj in bpy.context.scene.objects:
    if obj.type == 'MESH' and obj.name in ('Frame left', 'Frame right'):
        cx = -.032 if obj.name == 'Frame left' else .032
        obj.data.materials.append(transparent)
        index = len(obj.data.materials)-1
        count = 0
        for face in obj.data.polygons:
            if all(((obj.data.vertices[v].co.x-cx)/.025)**2 + (obj.data.vertices[v].co.z/.016)**2 <= 1.0001 for v in face.vertices):
                face.material_index = index
                count += 1
        assert count > 0, 'Fixture optical caps were not identified'
