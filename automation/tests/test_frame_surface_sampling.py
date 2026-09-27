import unittest
import numpy as np

from reconstruction.mesh import TriangleMesh
from reconstruction.frame_surface_coverage import (FrameSurfaceSamplingPolicy,sample_frame_surface,
                                                    assess_sampled_frame_surface_coverage)


def plane(divisions):
    axis=np.linspace(0,1,divisions+1);x,y=np.meshgrid(axis,axis)
    vertices=np.column_stack((x.ravel(),y.ravel(),np.zeros(x.size)))
    a=np.arange((divisions+1)**2).reshape(divisions+1,divisions+1)[:-1,:-1].ravel()
    faces=np.stack((np.column_stack((a,a+1,a+divisions+2)),
                    np.column_stack((a,a+divisions+2,a+divisions+1))),axis=1).reshape(-1,3)
    return TriangleMesh(vertices,faces,[{'face_start':0,'face_count':len(faces)}])


def measure(mesh, support=lambda xyz:np.ones(len(xyz),bool)):
    samples=sample_frame_surface(mesh,policy=FrameSurfaceSamplingPolicy(maximum_points=8192))
    valid=np.tile(support(samples['world']),(3,1))
    angles=np.radians([0,15,35]);view=np.column_stack((np.sin(angles),np.zeros(3),np.cos(angles)))
    directions=np.broadcast_to(view[:,None,:],(*valid.shape,3)).copy()
    return assess_sampled_frame_surface_coverage(mesh,samples,valid,directions),samples,valid,directions


class FrameSurfaceSamplingTests(unittest.TestCase):
    def test_full_observation_survives_retessellation_with_same_fixed_budget(self):
        reports=[measure(plane(n))[0] for n in (1,4,16,64)]
        for row in reports:
            self.assertEqual(row['samples'],8192)
            self.assertAlmostEqual(row['frame_area'],1.)
            self.assertEqual(row['estimated_observed_fraction'],1.)
            self.assertAlmostEqual(row['observed_fraction'],1-row['sampling_fraction_allowance'])
        self.assertEqual(len(set(row['observed_fraction'] for row in reports)),1)

    def test_partial_world_domain_is_stable_under_retessellation(self):
        reports=[measure(plane(n),lambda xyz:xyz[:,0]<.5)[0] for n in (1,4,16,64)]
        for row in reports:
            self.assertLess(abs(row['estimated_observed_fraction']-.5),row['sampling_fraction_allowance'])
            self.assertLess(row['observed_fraction'],.5)
            self.assertGreater(row['sampling_fraction_interval'][1],.5)

    def test_denominator_retains_large_ineligible_surface_and_excludes_optics(self):
        mesh=TriangleMesh(np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],
                                    [2.,0.,0.],[5.,0.,0.],[2.,3.,0.]]),
                          np.array([[0,1,2],[3,4,5]]),[{'face_start':0,'face_count':2}])
        row,_,_,_=measure(mesh,lambda xyz:xyz[:,0]<1.)
        self.assertEqual(row['frame_area'],5.)
        self.assertAlmostEqual(row['estimated_observed_fraction'],.1,delta=1/8192)
        self.assertLess(row['observed_fraction'],.1)
        mesh.parts=[{'face_start':0,'face_count':1},{'face_start':1,'face_count':1,'transmission':1.}]
        row,_,_,_=measure(mesh)
        self.assertEqual(row['frame_area'],.5)
        self.assertEqual(row['estimated_observed_fraction'],1.)

    def test_two_views_parallel_views_and_unknown_membership_cannot_earn_area(self):
        mesh=plane(1);_,samples,valid,directions=measure(mesh)
        for mask,view in ((valid[:2],directions[:2]),(valid,np.broadcast_to([0,0,1],directions.shape)),
                          (np.zeros_like(valid),directions)):
            self.assertEqual(assess_sampled_frame_surface_coverage(mesh,samples,mask,view)['observed_fraction'],0.)

    def test_selected_or_mutated_surface_sample_recipe_is_rejected(self):
        mesh=plane(4);_,samples,valid,directions=measure(mesh)
        samples['barycentric'][0]=[1.,0.,0.]
        with self.assertRaisesRegex(ValueError,'fixed complete-area recipe'):
            assess_sampled_frame_surface_coverage(mesh,samples,valid,directions)


if __name__=='__main__':
    unittest.main()
