import unittest
import numpy as np
from reconstruction.mesh import TriangleMesh
from reconstruction.frame_surface_coverage import assess_frame_surface_coverage, _subtriangles


def fixture():
    return TriangleMesh(np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],
                                  [2.,0.,0.],[5.,0.,0.],[2.,3.,0.]]),
        np.array([[0,1,2],[3,4,5]]), [{'face_start':0,'face_count':2,'transmission':0}])


def evidence(face=0):
    points = []
    for triangle in _subtriangles(4):
        for bary in ([.8,.1,.1],[.1,.8,.1],[.1,.1,.8],[1/3,1/3,1/3]):
            xy = np.asarray(bary) @ triangle
            points.append([*xy, 1-xy.sum()])
    angles = np.radians([-30,0,30])
    directions = np.array([np.sin(angles),np.zeros(3),np.cos(angles)]).T
    return {'source_faces':np.full(len(points),face,dtype=int),'barycentric':np.array(points),
        'valid':np.ones((3,len(points)),bool),'directions':np.tile(directions[:,None,:],(1,len(points),1))}


class FrameSurfaceCoverageTests(unittest.TestCase):
    def test_untextured_large_surface_is_in_denominator(self):
        result = assess_frame_surface_coverage(fixture(), [evidence()])
        self.assertAlmostEqual(result['observed_fraction'], .1)
        self.assertTrue(result['denominator_complete'])
    def test_all_surface_spatial_and_angular_coverage(self):
        self.assertAlmostEqual(assess_frame_surface_coverage(fixture(), [evidence(0),evidence(1)])['observed_fraction'],1.)
    def test_duplicate_texel_density_cannot_inflate_area(self):
        self.assertAlmostEqual(assess_frame_surface_coverage(fixture(), [evidence()]*4)['observed_fraction'],.1)
    def test_sparse_centers_and_parallel_cameras_do_not_cover_area(self):
        item=evidence(); item['barycentric'][:]=[1/3,1/3,1/3]
        self.assertEqual(assess_frame_surface_coverage(fixture(),[item])['observed_fraction'],0.)
        item=evidence(); item['directions'][:]=[0,0,1]
        self.assertEqual(assess_frame_surface_coverage(fixture(),[item])['observed_fraction'],0.)
    def test_optics_are_excluded_but_missing_tracks_stay_uncovered(self):
        mesh=fixture();mesh.parts=[{'face_start':0,'face_count':1,'transmission':0},
                                  {'face_start':1,'face_count':1,'has_lens_appearance_extension':True}]
        self.assertAlmostEqual(assess_frame_surface_coverage(mesh,[evidence()])['observed_fraction'],1.)
        self.assertEqual(assess_frame_surface_coverage(mesh,[])['observed_fraction'],0.)
    def test_bad_barycentric_binding_is_rejected(self):
        item=evidence();item['barycentric'][0]=[1,1,1]
        with self.assertRaises(ValueError):assess_frame_surface_coverage(fixture(),[item])


if __name__=='__main__':unittest.main()
