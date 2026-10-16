"""Native fan planning with an early-index topology compaction."""
from pathlib import Path
import copy,sys,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from glasses3d.tests.blender.owner_surface import create,capture
from glasses3d.g3d import owner_mesh_bpy,owner_mesh as M,owner_surface as S,owner_normal_plan


with tempfile.TemporaryDirectory(prefix='g3d-normal-fans-') as temp:
    root=Path(temp);obj=create();mesh=obj.data
    vertices=[list(v.co) for v in mesh.vertices];polygons=[list(p.vertices) for p in mesh.polygons]
    mesh.clear_geometry();mesh.from_pydata([vertices[0],*vertices],[],[[v+1 for v in p] for p in polygons]);mesh.update()
    mesh.shade_smooth()
    for edge in mesh.edges:
        if set(edge.vertices)=={17,18}:edge.use_edge_sharp=True
    before,path=capture(root,'before');source=before['objects'][0]
    operation={'id':'clean','kind':'mesh_cleanup','target_ids':['O1'],'params':{'strength':1.},
        'scope':{'object_id':source['id'],'vertex_weights':[{'vertex_id':i,'weight':1.} for i in range(len(source['vertices_local']))],
                 'fixed_vertex_ids':[],'affected_polygon_ids':list(range(len(source['polygons'])))},'marking_attachments':[]}
    target,_=S.state(before,[operation]);planned=target['objects'][0]
    assert planned['_surface_vertex_remap'][18]==17
    assert (16,17) in owner_normal_plan.sharp_edges(planned)
    applied=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),'operations':[operation]},root)
    assert applied['status']=='ok',applied
    after,_=capture(root,'after');assert S.verify(before,after,[operation])['ok']
    assert (16,17) in owner_normal_plan.sharp_edges(after['objects'][0])
    witness=after['objects'][0]['normal_intent']
    assert witness['encoded_matches'] and witness['decoded_within_quantization']
    hostile=copy.deepcopy(after);attribute=next(a for a in hostile['objects'][0]['attributes'] if a['name']=='sharp_edge')
    attribute['values'][0][0]=not attribute['values'][0][0]
    try:S.verify(before,hostile,[operation])
    except M.OwnerMeshError:pass
    else:raise AssertionError('unplanned sharp-edge mutation passed scope verification')
print('OWNER_NORMAL_FANS_PASSED')
