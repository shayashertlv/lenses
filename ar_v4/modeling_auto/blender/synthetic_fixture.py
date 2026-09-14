"""Create independently authored synthetic test inputs. Never reads old pipelines."""
import json
import math
from pathlib import Path
import sys
import bpy
import bmesh


def mesh_obj(name, vertices, faces, material):
    data = bpy.data.meshes.new(name)
    data.from_pydata(vertices, [], faces); data.update()
    bm=bmesh.new(); bm.from_mesh(data)
    bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces)); bm.to_mesh(data); bm.free()
    obj = bpy.data.objects.new(name, data); bpy.context.collection.objects.link(obj)
    data.materials.append(material)
    for p in data.polygons: p.use_smooth = True
    return obj


def ring(name, cx, mat, fused=False):
    vertices, faces = [], []
    n = 64
    for y, a, b in ((-.003,.030,.021),(.003,.030,.021),(-.003,.025,.016),(.003,.025,.016)):
        for j in range(n):
            theta = j * math.tau/n
            vertices.append((cx + a*math.cos(theta),y,b*math.sin(theta)))
    for j in range(n):
        k=(j+1)%n
        faces.extend([(j,k,n+k,n+j), (j,2*n+j,2*n+k,k),(n+j,n+k,3*n+k,3*n+j)])
        if not fused:
            faces.append((2*n+j,3*n+j,3*n+k,2*n+k))
    if fused:
        faces.extend([tuple(2*n+j for j in range(n)), tuple(3*n+j for j in reversed(range(n)))])
    return mesh_obj(name,vertices,faces,mat)


def tube(name, points, radius, mat):
    from mathutils import Vector
    vertices, faces=[],[]
    n=12
    for idx, pos in enumerate(points):
        tangent = Vector(points[min(idx+1,len(points)-1)])-Vector(points[max(0,idx-1)])
        tangent.normalize()
        u=tangent.cross(Vector((0,0,1))).normalized(); v=tangent.cross(u).normalized()
        for j in range(n):
            p=Vector(pos)+radius*(math.cos(math.tau*j/n)*u+math.sin(math.tau*j/n)*v)
            vertices.append(tuple(p))
    for i in range(len(points)-1):
        for j in range(n):
            k=(j+1)%n; faces.append((i*n+j,i*n+k,(i+1)*n+k,(i+1)*n+j))
    faces.append(tuple(reversed(range(n))))
    faces.append(tuple((len(points)-1)*n+j for j in range(n)))
    return mesh_obj(name,vertices,faces,mat)


def main():
    args=sys.argv[sys.argv.index('--')+1:]
    output=Path(args[0]).resolve(); output.mkdir(parents=True,exist_ok=True)
    root=Path(__file__).resolve().parents[1]
    if not output.is_relative_to(root): raise ValueError('Output outside modeling_auto')
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    mat=bpy.data.materials.new('Synthetic untextured frame'); mat.use_nodes=True
    bsdf=mat.node_tree.nodes.get('Principled BSDF')
    bsdf.inputs['Base Color'].default_value=(.24,.26,.30,1); bsdf.inputs['Roughness'].default_value=.38
    fused='--fused' in args
    ring('Frame left',-.032,mat,fused); ring('Frame right',.032,mat,fused)
    tube('Bridge',[(-.010,0,.009),(-.005,0,.011),(0,0,.012),(.005,0,.011),(.010,0,.009)],.002,mat)
    for side in (-1,1):
        tube('Temple '+str(side),[(side*.061,0,.001),(side*.065,.02,.001),(side*.066,.08,-.002),(side*.062,.125,-.01)],.002,mat)
    bpy.ops.wm.save_as_mainfile(filepath=str(output/'blank.blend'))
    bpy.ops.export_scene.gltf(filepath=str(output/'blank.glb'),export_format='GLB',export_yup=True)
    (output/'fixture.json').write_text(json.dumps({'synthetic':True,'fused_optics':fused,'source':'independent procedural fixture','vertices':sum(len(o.data.vertices) for o in bpy.context.scene.objects if o.type=='MESH')}),encoding='utf-8')

if __name__=='__main__': main()
