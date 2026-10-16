"""Real Blender radius, smaller radius and exact original sharp-corner restoration."""
import copy
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import bpy
from glasses3d.tests.blender.owner_surface import capture
from glasses3d.g3d import owner_mesh as M,owner_mesh_bpy,owner_surface as S


def main():
    vertices=[[-2,-2,-1],[2,-2,-1],[2,2,-1],[-2,2,-1],[-2,-2,1],[2,-2,1],[2,2,1],[-2,2,1]]
    polygons=[[0,3,2,1],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7],[4,5,6,7]]
    operation={'id':'radius','kind':'edge_shape','target_ids':['E1'],'marking_attachments':[],
               'params':{'strength':1.,'mode':'round','radius_mm':.2,'edge_vertex_ids':[4,5],'segments':4,'minimum_mm':1.5}}
    bpy.ops.wm.read_factory_settings(use_empty=True);scene=bpy.context.scene
    scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=.001;scene['mdl_bridge_underside']=[0.,0.,0.]
    mesh=bpy.data.meshes.new('Edge test');mesh.from_pydata(vertices,[],polygons);mesh.update()
    obj=bpy.data.objects.new('Edge test',mesh);scene.collection.objects.link(obj);obj['partRole']='frame'
    with tempfile.TemporaryDirectory(prefix='g3d-radius-') as temp:
        root=Path(temp);original,_=capture(root,'original');results=[]
        for label,radius in [('round',.2),('smaller',.1),('sharp',0.)]:
            before,path=capture(root,label+'-before');row=before['objects'][0];op=copy.deepcopy(operation)
            op['scope']={'object_id':row['id'],'vertex_weights':[{'vertex_id':i,'weight':1.} for i in range(len(row['vertices_local']))],
                         'fixed_vertex_ids':[],'affected_polygon_ids':list(range(len(row['polygons'])))}
            if label!='round':op['params']={'strength':1.,'mode':'sharpen','radius_mm':radius,'minimum_mm':1.5}
            result=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),'operations':[op]},root)
            assert result['status']=='ok',result
            after,_=capture(root,label+'-after');checked=S.verify(before,after,[op]);assert checked['ok']
            results.append({'label':label,'radius_mm':radius,'vertices':len(after['objects'][0]['vertices_local'])})
        assert after['objects'][0]['vertices_local']==original['objects'][0]['vertices_local']
        assert after['objects'][0]['polygons']==original['objects'][0]['polygons']
        print('OWNER_RADIUS_RESULT '+json.dumps(results))


if __name__=='__main__':main()
