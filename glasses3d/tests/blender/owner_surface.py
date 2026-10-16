"""Real Blender regression for isolated surface operation arrays and scope validation."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import bpy
import numpy as np
from glasses3d.g3d import model_map_bpy, owner_mesh_bpy, owner_mesh as M, owner_surface as S


def create():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=.001
    scene['mdl_bridge_underside']=[0.,0.,0.]
    v=[[x,y,2+.08*x*x+.05*y*y] for y in (-2.,0.,2.) for x in (-2.,0.,2.)]
    v += [[x,y,0.] for y in (-2.,0.,2.) for x in (-2.,0.,2.)]
    p=[[y*3+x,y*3+x+1,(y+1)*3+x+1,(y+1)*3+x] for y in (0,1) for x in (0,1)]
    p += [[i+9 for i in q[::-1]] for q in p.copy()]
    ring=[0,1,2,5,8,7,6,3]
    p += [[b,a,a+9,b+9] for a,b in zip(ring,ring[1:]+ring[:1])]
    me=bpy.data.meshes.new('Surface');me.from_pydata(v,[],p);me.update()
    obj=bpy.data.objects.new('Surface',me);scene.collection.objects.link(obj);obj['partRole']='frame'
    uv=me.uv_layers.new(name='UVMap');uv.data.foreach_set('uv',np.tile([0,0,1,0,1,1,0,1],len(p)))
    mat=bpy.data.materials.new('Original');mat.use_nodes=True;me.materials.append(mat)
    return obj


def capture(root,label):
    path=root/'work/model_maps'/label/'snapshot.json'
    result=model_map_bpy.snapshot({'path':str(path)},root)
    assert result['status']=='ok',result
    return json.loads(path.read_text()),path


def run():
    checks={}
    with tempfile.TemporaryDirectory(prefix='g3d-surface-') as temp:
        root=Path(temp)
        for kind,params in [('flatten_face',{'strength':1.,'keep_boundary':False}),
                            ('repair_shading',{'strength':1.,'mode':'smooth'}),
                            ('refine_curved_surface',{'strength':1.}),
                            ('material_adjust',{'strength':1.,'material_index':0,'values':{'roughness':.2}}),
                            ('thickness',{'strength':1.,'offset_mm':-.1,'minimum_mm':1.}),
                            ('scale_region',{'strength':1.,'factors':[1.02,1.,1.],'anchor_mm':[0,0,2.]}),
                            ('bend_region',{'strength':1.,'axis':[0,0,1],'direction':[0,1,0],'anchor_mm':[0,-2,0.],'angle_deg':2.,'length_mm':4.})]:
            obj=create()
            if kind=='thickness':
                for v in obj.data.vertices[:9]:v.co.z=2.
                obj.data.update()
            before,path=capture(root,kind+'-before');source=before['objects'][0]
            ids=set(range(18 if kind in ('material_adjust','scale_region','bend_region') else 9))
            scope={'object_id':source['id'],'vertex_weights':[{'vertex_id':i,'weight':1.} for i in ids],
                   'fixed_vertex_ids':[],'affected_polygon_ids':[i for i,p in enumerate(source['polygons']) if set(p)&ids]}
            op={'id':kind,'kind':kind,'params':params,'target_ids':['T1'],'scope':scope,'marking_attachments':[]}
            result=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),'operations':[op]},root)
            assert result['status']=='ok',result
            after,_=capture(root,kind+'-after')
            checked=S.verify(before,after,[op])
            assert checked['ok'],checked
            if kind=='repair_shading':assert source['vertices_local']==after['objects'][0]['vertices_local']
            checks[kind]={'ok':True,'before_vertices':len(source['vertices_local']),
                          'after_vertices':len(after['objects'][0]['vertices_local'])}
    print('OWNER_SURFACE_RESULT '+json.dumps(checks))


if __name__=='__main__':run()
