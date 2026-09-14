import bpy
for obj in bpy.context.scene.objects:
    if obj.type == 'MESH' and obj.get('auto_role') in ('lens_left', 'lens_right'):
        center_x = -.032 if obj.get('auto_role') == 'lens_left' else .032
        for vertex in obj.data.vertices:
            vertex.co.x = center_x + (vertex.co.x-center_x) * 1.006
            vertex.co.z *= 1.006
        obj.data.update()
