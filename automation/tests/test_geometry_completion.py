import unittest
import numpy as np
from reconstruction.camera import Camera
from reconstruction.mesh import TriangleMesh
from reconstruction.raster import rasterize
from reconstruction.geometry_completion import boundary_loops, bridge_boundary_curves, propose_missing_geometry, append_constructed_geometry
from reconstruction.mesh import load_glb_bytes
from test_surface_transfer import build_generation


class CompletionTests(unittest.TestCase):
    def fixture(self):
        angle=np.arange(16)*np.pi/8
        ring=lambda x:np.column_stack((np.full(16,x),.012*np.cos(angle),.012*np.sin(angle)))
        a=bridge_boundary_curves(ring(-.5),ring(-.035))
        b=bridge_boundary_curves(ring(.035),ring(.5))
        mesh=TriangleMesh(np.r_[a.vertices,b.vertices],np.r_[a.faces,b.faces+len(a.vertices)],[])
        whole=bridge_boundary_curves(ring(-.5),ring(.5));views=[]
        for i,yaw in enumerate((0,25)):
            camera=Camera(yaw,0,0,0,400,240,100);mask=rasterize(whole,camera,(200,480)).mask
            views.append({'id':str(i),'camera':camera,'shape':mask.shape,'frame_mask':mask,'lens_mask':np.zeros_like(mask)})
        return mesh,views

    def test_connects_bounded_missing_frame_gap_with_two_views(self):
        mesh,views=self.fixture();result=propose_missing_geometry(mesh,views)
        self.assertEqual(len(result['proposals']),1)
        self.assertEqual(result['proposals'][0]['kind'],'frame_curve_bridge')

    def test_missing_or_contrary_evidence_never_constructs(self):
        mesh,views=self.fixture()
        self.assertFalse(propose_missing_geometry(mesh,views[:1])['proposals'])
        views[1]['frame_mask'][:]=False
        self.assertFalse(propose_missing_geometry(mesh,views)['proposals'])

    def test_constructed_gap_roundtrip_retains_original_faces_vertices_and_uv(self):
        mesh,views=self.fixture();result=propose_missing_geometry(mesh,views)
        raw=build_generation(mesh.vertices,mesh.vertices[:,:2],mesh.faces)
        actual=load_glb_bytes(append_constructed_geometry(raw,result['proposals'],center=np.zeros(3),extent=1.))
        source=load_glb_bytes(raw)
        np.testing.assert_array_equal(actual.vertices[:len(source.vertices)],source.vertices)
        np.testing.assert_array_equal(actual.faces[:len(source.faces)],source.faces)
        self.assertGreater(len(actual.faces),len(source.faces))

    def test_moving_arm_gap_abstains_without_endpoint_role_binding(self):
        mesh,views=self.fixture();views[0]['moving_parts']=True
        self.assertFalse(propose_missing_geometry(mesh,views)['proposals'])

    def test_irregular_unequal_end_loops_keep_every_measured_vertex_and_edge(self):
        first=np.array([[0,0,0],[0,.02,0],[0,.02,.013],[0,0,.019]])
        angles=np.array([0,.25,1.4,2.1,3.6,4.7,5.9])
        second=np.column_stack((np.full(len(angles),.025),.01+.012*np.cos(angles),.009+.01*np.sin(angles)))
        for steps in (2,8):
            tube=bridge_boundary_curves(first,second,steps=steps)
            loops=boundary_loops(tube)
            self.assertEqual(len(loops),2)
            actual={frozenset(map(tuple,loop)) for loop in loops}
            self.assertEqual(actual,{frozenset(map(tuple,first)),frozenset(map(tuple,second))})
            edges=np.concatenate([tube.faces[:,[0,1]],tube.faces[:,[1,2]],tube.faces[:,[2,0]]])
            edges,counts=np.unique(np.sort(edges,axis=1),axis=0,return_counts=True)
            self.assertTrue(np.all((counts==1)|(counts==2)))
            boundary={frozenset(map(tuple,tube.vertices[edge])) for edge in edges[counts==1]}
            expected={frozenset((tuple(loop[i]),tuple(loop[(i+1)%len(loop)])))
                      for loop in (first,second) for i in range(len(loop))}
            self.assertEqual(boundary,expected)

    def test_bridge_welds_to_nonuniform_source_boundaries_without_corner_gaps(self):
        shape=np.array([[0,0,0],[0,.02,0],[0,.02,.013],[0,0,.019]])
        a=bridge_boundary_curves(shape+[-.5,0,0],shape+[-.035,0,0])
        b=bridge_boundary_curves(shape+[.035,0,0],shape+[.5,0,0])
        patch=bridge_boundary_curves(shape+[-.035,0,0],shape+[.035,0,0])
        vertices=np.concatenate([a.vertices,b.vertices,patch.vertices])
        faces=np.concatenate([a.faces,b.faces+len(a.vertices),patch.faces+len(a.vertices)+len(b.vertices)])
        self.assertEqual(len(boundary_loops(TriangleMesh(vertices,faces,[]))),2)


if __name__=='__main__':unittest.main()
