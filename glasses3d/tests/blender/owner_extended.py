"""Actual Blender marking transforms follow their frozen supporting surface."""
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
import bpy
from glasses3d.tests.blender.owner_surface import create,capture
from glasses3d.g3d import owner_mesh as M,owner_mesh_bpy,owner_markings,owner_surface as S


def main():
    with tempfile.TemporaryDirectory(prefix='g3d-mark-transform-') as folder:
        root=Path(folder);surface=create()
        for vertex in surface.data.vertices:
            if vertex.co.z>1:vertex.co.z=2.
        surface.data.update()
        mesh=bpy.data.meshes.new('Mark');mesh.from_pydata([[-.4,-.2,2.02],[.4,-.2,2.02],[.4,.2,2.02],[-.4,.2,2.02]],[],[[0,1,2,3]])
        marker=bpy.data.objects.new('Mark',mesh);bpy.context.scene.collection.objects.link(marker);marker.parent=surface;marker['partRole']='marking'
        before,path=capture(root,'before');rows={r['name']:r for r in before['objects']};mark=rows['Mark'];support=rows['Surface']
        op={'id':'move-mark','kind':'transform_marking','target_ids':['M1'],'scope':{'object_id':mark['id'],
            'vertex_weights':[{'vertex_id':i,'weight':1.} for i in range(4)],'fixed_vertex_ids':[],'affected_polygon_ids':[0]},
            'params':{'strength':1.,'surface_object_id':support['id'],'anchor_mm':[0,0,2.],'u_axis':[1,0,0],'normal':[0,0,1],
            'translate_uv_mm':[.2,.1],'scale':[1.1,.9],'angle_deg':10.}}
        operations=owner_markings.seal(before,[op]);result=owner_mesh_bpy.apply({'snapshot_path':str(path),'snapshot_sha256':M.digest(before),'operations':operations},root)
        assert result['status']=='ok',result
        after,_=capture(root,'after');check=S.verify(before,after,operations)
        assert check['ok'];assert M.objects(after)[support['id']]==support
        print('OWNER_MARK_TRANSFORM '+json.dumps(check['changes']))


if __name__=='__main__':main()
