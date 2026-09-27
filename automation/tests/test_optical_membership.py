import unittest
import numpy as np
from scipy.ndimage import binary_dilation

from reconstruction.camera import Camera
from reconstruction.mesh import TriangleMesh
from reconstruction.raster import rasterize
from reconstruction.optical_membership import audit_optical_membership
from test_optical_group_raster import boxes


class OpticalMembershipTests(unittest.TestCase):
    def fixture(self):
        mesh, labels = boxes([(-.4,.3,-.3,.3,-.01,0,0),
                              (.3,.42,-.3,.3,-.01,0,1),
                              (-.1,.1,-.1,.1,-.3,-.2,2),
                              (-.5,.5,.28,.4,0,.01,3)])
        optical = TriangleMesh(mesh.vertices, mesh.faces[labels<2], [])
        views=[]
        for i,yaw in enumerate((0,25)):
            camera=Camera(yaw,0,0,0,90,64,64)
            mask=binary_dilation(rasterize(optical,camera,(128,128)).mask,iterations=1)
            views.append({'id':str(i),'camera':camera,'shape':mask.shape,
                          'interpretations':[{'mask':mask,'known_domain':np.ones_like(mask)}]})
        return mesh,labels,[{'group_id':'lens','members':[0]}],views

    def test_missing_fragment_is_attached_without_promoting_rear_hardware_or_frame(self):
        mesh,labels,groups,views=self.fixture()
        report=audit_optical_membership(mesh,labels,groups,views)
        self.assertEqual([a['component_id'] for a in report['assignments']],[1])
        self.assertEqual(report['assignments'][0]['group_id'],'lens')
        self.assertTrue(all(v['opaque_core_pixels_after_proposal']<v['opaque_core_pixels_before'] for v in report['views']))
        self.assertFalse(report['accepted'])

    def test_one_view_or_repeated_camera_cannot_establish_fragment(self):
        mesh,labels,groups,views=self.fixture()
        self.assertFalse(audit_optical_membership(mesh,labels,groups,views[:1])['assignments'])
        duplicate=[views[0],{**views[0],'id':'duplicate'}]
        self.assertFalse(audit_optical_membership(mesh,labels,groups,duplicate)['assignments'])

    def test_contrary_view_blocks_attachment_and_empty_proposals_fail(self):
        mesh,labels,groups,views=self.fixture()
        views[1]['interpretations'][0]['mask'][:]=False
        self.assertFalse(audit_optical_membership(mesh,labels,groups,views)['assignments'])
        views[1]['interpretations']=[]
        with self.assertRaises(ValueError):audit_optical_membership(mesh,labels,groups,views)


if __name__=='__main__':unittest.main()
