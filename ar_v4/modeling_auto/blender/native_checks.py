"""Fast real-interpreter capability and geometry-lock checks, no remote services."""
import json
import math
from pathlib import Path
import sys
import bpy
import bmesh
import mathutils

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'blender'))
from app.script_validation import validate_script,safe_builtins,ALLOWED_IMPORTS
import worker

fixture=Path(sys.argv[sys.argv.index('--')+1]).resolve()
output=Path(sys.argv[sys.argv.index('--')+2]).resolve()
if not fixture.is_relative_to(ROOT) or not output.is_relative_to(ROOT): raise ValueError('Outside modeling_auto')
results=[]


def execute(script):
    validate_script(script)
    ns={'__builtins__':safe_builtins(),'bpy':bpy,'bmesh':bmesh,'math':math,'mathutils':mathutils}
    exec(compile(script,'<native-capability>','exec'),ns,ns)


def reset():
    bpy.ops.wm.open_mainfile(filepath=str(fixture))
    return worker.geometry_snapshot()


def rejects(name,stage,change):
    before=reset(); lo,hi=worker.bounds(); execute(change); bpy.context.view_layer.update()
    try: worker.enforce_scope(before,stage,max(hi-lo))
    except ValueError as exc: results.append({'name':name,'ok':True,'rejection':str(exc)})
    else: raise AssertionError(name+' escaped geometry guard')


def rejects_scene(name,change):
    reset(); before=worker.protected_scene_state(); execute(change); bpy.context.view_layer.update()
    try: worker.enforce_scene_state(before)
    except ValueError as exc: results.append({'name':name,'ok':True,'rejection':str(exc)})
    else: raise AssertionError(name+' escaped scene guard')


for module in sorted(ALLOWED_IMPORTS):
    execute('import '+module)
    results.append({'name':'import '+module,'ok':True})
execute("from mathutils.bvhtree import BVHTree\nfrom mathutils.kdtree import KDTree\nfrom mathutils.geometry import intersect_ray_tri\nfrom mathutils import Vector\ntree=BVHTree.FromPolygons([(0,0,0),(1,0,0),(0,1,0)],[(0,1,2)])\nassert tree.find_nearest((.2,.2,.1))[0] is not None\nkd=KDTree(1)\nkd.insert((0,0,0),0)\nkd.balance()\nassert kd.find((0,0,1))[1] == 0\nassert intersect_ray_tri(Vector((0,0,0)),Vector((1,0,0)),Vector((0,1,0)),Vector((0,0,-1)),Vector((.2,.2,1)),True) is not None")
results.append({'name':'BVH, KDTree and geometry queries','ok':True})
reset()
execute("import bpy\nbpy.ops.object.select_all(action='DESELECT')\nobj=next(o for o in bpy.context.scene.objects if o.type=='MESH')\nobj.select_set(True)\nbpy.context.view_layer.objects.active=obj\nbpy.ops.object.mode_set(mode='EDIT')\nbpy.ops.mesh.select_all(action='SELECT')\nbpy.ops.object.mode_set(mode='OBJECT')\nbpy.ops.object.shade_smooth()\nmodifier=obj.modifiers.new('Bounded displacement probe','SMOOTH')\nmodifier.factor=.0001\nbpy.ops.object.modifier_apply(modifier=modifier.name)")
results.append({'name':'all five permitted Blender operators','ok':True})
before=reset(); execute("import bpy\nfor material in bpy.data.materials:\n    if material.use_nodes:\n        material.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=.19")
lo,hi=worker.bounds(); worker.enforce_scope(before,'finish',max(hi-lo)); results.append({'name':'material-only exact geometry','ok':True})
prefix="import bpy\nobj=next(o for o in bpy.context.scene.objects if o.type=='MESH')\n"
rejects('finish vertex','finish',prefix+'obj.data.vertices[0].co.x += .0001')
rejects('finish visibility','finish',prefix+'obj.hide_render=True')
rejects('finish UV','finish',prefix+"uv=obj.data.uv_layers.new(name='unexpected')")
rejects('finish evaluated modifier','finish',prefix+"m=obj.modifiers.new('changed','SOLIDIFY')\nm.thickness=.001")
rejects('source transform','lenses',prefix+'obj.scale.x=1.1')
rejects('source name','lenses',prefix+"obj.name='rebuilt'")
rejects('source role bypass','lenses',prefix+"obj['auto_role']='lens_left'")
rejects('source large smoothing','lenses',prefix+'obj.data.vertices[0].co.x += .01')
rejects('connection source coordinates','connections',prefix+'obj.data.vertices[0].co.x += .0001')
rejects_scene('new light',"import bpy\nlight=bpy.data.lights.new('Misleading','AREA')\nobj=bpy.data.objects.new('Misleading',light)\nbpy.context.collection.objects.link(obj)")
rejects_scene('new camera',"import bpy\ncam=bpy.data.cameras.new('Misleading')\nobj=bpy.data.objects.new('Misleading',cam)\nbpy.context.collection.objects.link(obj)")
rejects_scene('world brightness',"import bpy\nbpy.context.scene.world.color=(1,0,0)")
rejects_scene('render alteration',"import bpy\nbpy.context.scene.render.resolution_percentage=50")
rejects_scene('collection hiding',"import bpy\nnext(iter(bpy.data.collections)).hide_render=True")
rejects_scene('new collection',"import bpy\nbpy.context.scene.collection.children.link(bpy.data.collections.new('Hidden source'))")
output.parent.mkdir(parents=True,exist_ok=True)
output.write_text(json.dumps({'ok':True,'checks':len(results),'results':results,'paid_calls':0},indent=2),encoding='utf-8')
print('NATIVE_CHECKS_OK',len(results))
