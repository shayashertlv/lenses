import unittest
import numpy as np
from reconstruction.camera import Camera
from reconstruction.mesh import TriangleMesh
from reconstruction.raster import rasterize
from reconstruction.face_role_repair import propose_face_roles, split_component_ledger


def grid(x0,x1,y0,y1,n=12,z=0.):
    vertices=np.array([[x,y,z] for y in np.linspace(y0,y1,n) for x in np.linspace(x0,x1,n)])
    faces=[]
    for j in range(n-1):
        for i in range(n-1):
            p=j*n+i;faces.extend([[p,p+1,p+n+1],[p,p+n+1,p+n]])
    return vertices,np.array(faces)


class FaceRoleTests(unittest.TestCase):
    def fixture(self):
        a,fa=grid(-.4,.1,-.3,.3)
        b,fb=grid(.1,.3,-.3,.3)
        c,fc=grid(.1,.3,.3,.45)
        vertices=np.concatenate((a,b,c));faces=np.concatenate((fa,fb+len(a),fc+len(a)+len(b)))
        mesh=TriangleMesh(vertices,faces,[]);labels=np.r_[np.zeros(len(fa),int),np.ones(len(fb)+len(fc),int)]
        optical=TriangleMesh(vertices,faces[:len(fa)+len(fb)],[])
        views=[]
        for i,yaw in enumerate((0,25)):
            camera=Camera(yaw,0,0,0,220,100,100);mask=rasterize(optical,camera,(220,220)).mask
            views.append({'id':str(i),'camera':camera,'shape':mask.shape,'interpretations':[{'mask':mask,'known_domain':np.ones_like(mask)}]})
        return mesh,labels,[{'group_id':'lens','members':[0]}],views,len(fa),len(fb)

    def test_splits_only_photo_supported_region_of_fused_component(self):
        mesh,labels,groups,views,n,m=self.fixture()
        report=propose_face_roles(mesh,labels,groups,views)
        ids=np.concatenate([a['source_face_indices'] for a in report['assignments']])
        self.assertGreater(len(ids),20)
        self.assertTrue(np.all((ids>=n)&(ids<n+m)))
        self.assertFalse(report['accepted'])

    def test_contrary_or_repeated_view_cannot_split(self):
        mesh,labels,groups,views,_,_=self.fixture()
        self.assertFalse(propose_face_roles(mesh,labels,groups,[views[0],views[0]])['assignments'])
        views[1]['interpretations'][0]['mask'][:]=False
        self.assertFalse(propose_face_roles(mesh,labels,groups,views)['assignments'])

    def test_virtual_ledger_preserves_every_face_and_rejects_overlap(self):
        scene={'face_components':np.array([0,0,1,1,1]),'primitive_labels':{(0,0,0):np.array([0,0,1,1,1])},
               'component_table':[{'component_id':i,'local_component_id':i,'source_face_count':n,
                    'source_global_face_offset':0,'source_binding':dict(node_index=0,mesh_index=0,primitive_index=0)} for i,n in enumerate((2,3))]}
        hypothesis={'groups':[{'group_id':'lens','members':[0]},{'group_id':'frame','members':[1]}]}
        assignments=[{'group_id':'lens','source_face_indices':[2,3]}]
        revised,h=split_component_ledger(scene,hypothesis,assignments)
        self.assertEqual(revised['face_components'].tolist(),[0,0,2,2,1])
        self.assertEqual(sum(r['source_face_count'] for r in revised['component_table']),5)
        self.assertEqual(h['groups'][0]['members'],[0,2])
        self.assertEqual(scene['face_components'].tolist(),[0,0,1,1,1])
        with self.assertRaises(ValueError):split_component_ledger(scene,hypothesis,assignments*2)


if __name__=='__main__':unittest.main()
