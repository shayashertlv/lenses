import bpy
import math
import bmesh

# This deterministic fixture uses its independently authored rim coordinates.
# Real Astra scripts must query the supplied model instead.
for side, role in ((-1, 'lens_left'), (1, 'lens_right')):
    vertices = []
    faces = []
    n = 64
    rings = 8
    for back in (False, True):
        for ring in range(1, rings + 1):
            r = ring / rings
            for j in range(n):
                t = math.tau * j / n
                vertices.append((side * .032 + .025 * r * math.cos(t),
                                 -.001 - .002 * (1-r*r) + (.0018 if back else 0),
                                 .016 * r * math.sin(t)))
    front_center = len(vertices)
    vertices.append((side * .032, -.003, 0))
    back_center = len(vertices)
    vertices.append((side * .032, -.0012, 0))
    offset = n * rings
    for j in range(n):
        k = (j+1) % n
        faces.append((front_center, j, k))
        faces.append((back_center, offset+k, offset+j))
        for r in range(rings-1):
            a=r*n; b=(r+1)*n
            faces.append((a+j,b+j,b+k,a+k))
            faces.append((offset+a+j,offset+a+k,offset+b+k,offset+b+j))
        a=(rings-1)*n
        faces.append((a+j,offset+a+j,offset+a+k,a+k))
    mesh = bpy.data.meshes.new(role)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(role, mesh)
    bpy.context.collection.objects.link(obj)
    obj['auto_role'] = role
    material = bpy.data.materials.new(role + ' optical')
    material.use_nodes = True
    shader = material.node_tree.nodes.get('Principled BSDF')
    shader.inputs['Base Color'].default_value = (.75,.88,.96,1)
    shader.inputs['Transmission Weight'].default_value = .92
    shader.inputs['Roughness'].default_value = .08
    shader.inputs['IOR'].default_value = 1.46
    mesh.materials.append(material)
    for poly in mesh.polygons:
        poly.use_smooth = True
