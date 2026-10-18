"""Native OE-03 parity, OE-04 no-op and OE-09 normal-modifier evidence."""
import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import bpy
from glasses3d.g3d import owner_mesh as M, owner_mesh_bpy, owner_surface as S
from glasses3d.tests.blender.owner_surface import capture


def create():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene=bpy.context.scene;scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=.001
    scene['mdl_bridge_underside']=[0.,0.,0.]
    for name,x in [('A',0),('B',20)]:
        vertices=[[-2,-2,-1],[2,-2,-1],[2,2,-1],[-2,2,-1],[-2,-2,1],[2,-2,1],[2,2,1],[-2,2,1]]
        polygons=[[0,3,2,1],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7],[4,5,6,7]]
        mesh=bpy.data.meshes.new(name);mesh.from_pydata(vertices,[],polygons);mesh.update()
        obj=bpy.data.objects.new(name,mesh);scene.collection.objects.link(obj)
        obj.location.x=x;obj['partRole']='frame'


def op(obj,kind,params):
    return {'id':obj['name']+'-'+kind,'kind':kind,'params':params,'target_ids':[obj['id']],
        'scope':{'object_id':obj['id'],'vertex_weights':[{'vertex_id':i,'weight':1.} for i in range(len(obj['vertices_local']))],
                 'fixed_vertex_ids':[],'affected_polygon_ids':list(range(len(obj['polygons'])))},'marking_attachments':[]}


def apply(root,source,path,ops,label):
    result=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(source),'operations':ops},root)
    assert result['status']=='ok',result
    after,newpath=capture(root,label)
    assert S.verify(source,after,ops)['ok']
    return after,newpath


with tempfile.TemporaryDirectory(prefix='g3d-geometry-review-') as temp:
    root=Path(temp);create();source,path=capture(root,'batch-source')
    move=op(source['objects'][0],'translate_region',{'delta_mm':[.125,0,0]})
    repair=op(source['objects'][1],'repair_shading',{'strength':1.,'mode':'smooth'})
    batch,_=apply(root,source,path,[move,repair],'batch')
    create();source,path=capture(root,'sequential-source')
    first,path=apply(root,source,path,[move],'sequential-first')
    sequential,_=apply(root,first,path,[repair],'sequential-final')
    for a,b in zip(batch['objects'],sequential['objects']):
        for key in ('vertices_local','polygons','corner_normals','polygon_smooth','has_custom_normals','attributes','normal_intent'):
            assert a[key]==b[key],key
    # An existing intent witness belongs to the captured geometry. Subsequent
    # operations must use the newly authored directions, not inherit stale ones.
    create();source,path=capture(root,'same-object-source')
    surface=source['objects'][0]
    smooth=op(surface,'repair_shading',{'strength':1.,'mode':'smooth'})
    flat=op(surface,'repair_shading',{'strength':1.,'mode':'flat'})
    stretch=op(surface,'scale_region',{'strength':1.,'factors':[1.125,1.,1.],'anchor_mm':[0.,0.,0.]})
    initial,path=apply(root,source,path,[smooth],'same-object-initial')
    batch_same,_=apply(root,initial,path,[stretch,flat,stretch],'same-object-batch')
    create();source,path=capture(root,'same-object-reset')
    first,path=apply(root,source,path,[smooth],'same-object-seeded')
    for i,operation in enumerate((stretch,flat,stretch)):
        first,path=apply(root,first,path,[operation],'same-object-step-'+str(i))
    for a,b in zip(batch_same['objects'],first['objects']):
        for key in ('vertices_local','polygons','corner_normals','polygon_smooth','has_custom_normals','attributes','normal_intent'):
            assert a[key]==b[key],('same object',key)
    create();source,path=capture(root,'private-source')
    historical=copy.deepcopy(batch)
    for row in historical['objects']:row.pop('unsupported_normal_edit_state',None)
    historical_path=root/'historical-target.json'
    historical_path.write_text(json.dumps(historical),encoding='utf-8')
    result=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(source),
        'operations':[move,repair],'target_snapshot_path':str(historical_path),
        'target_snapshot_sha256':M.digest(historical)},root)
    assert result['status']=='ok',result
    transferred,_=capture(root,'private-after')
    assert S.signature(transferred)==S.signature(batch)
    create();obj=bpy.data.objects['A'];obj.data.shade_smooth()
    source,path=capture(root,'consistent-source')
    consistent=op(source['objects'][0],'consistent_normals',{'strength':1.})
    target,_=apply(root,source,path,[consistent],'consistent-after')
    for key in ('corner_normals','has_custom_normals','attributes'):
        assert target['objects'][0][key]==source['objects'][0][key],key
    obj.modifiers.new('Override authored normals','WEIGHTED_NORMAL')
    material=bpy.data.materials.new('Weighted surface');material.use_nodes=True;obj.data.materials.append(material)
    modified,path=capture(root,'weighted')
    row=modified['objects'][0]
    assert any('Normal-changing modifier' in message for message in row.get('unsupported_normal_edit_state',row['unsupported_edit_state']))
    material_op=op(row,'material_adjust',{'strength':1.,'material_index':0,'values':{'roughness':.25}})
    recolored,_=apply(root,modified,path,[material_op],'weighted-material')
    assert recolored['objects'][0]['evaluated']['corner_normals']==row['evaluated']['corner_normals']
    assert not row['unsupported_edit_state']
    try:S.state(modified,[op(row,'repair_shading',{'strength':1.,'mode':'flat'})])
    except M.OwnerMeshError:pass
    else:raise AssertionError('normal-overriding modifier accepted a raw shading repair')
print('OWNER_GEOMETRY_REVIEW_PASSED')
if '--' in sys.argv and len(sys.argv)>sys.argv.index('--')+1:
    payload=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text(encoding='utf-8'))
    print('TRANSPORT_PARITY='+json.dumps(S.transport_normals(payload['object'],payload['after'])))
