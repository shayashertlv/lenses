import dataclasses
import unittest

import numpy as np

from qa.structured_geometry_probe import synthetic_fixture
from reconstruction.camera import project
from reconstruction.mesh import TriangleMesh
from reconstruction.structured_refinement import (StructuredPolicy,
    assess_geometry_candidate, fit_front_cameras, fit_temple_poses, fit_shared_geometry,
    propose_part_bindings, classify_geometry_failure)
from reconstruction.view_scene import Hinge, ViewState, bind_parts, pose_scene


class StructuredGeometryTests(unittest.TestCase):
    def setUp(self):
        self.scene, self.binding, self.evidence, self.cameras, self.states, self.seeds = synthetic_fixture()

    def test_mixed_pose_recovered_without_changing_rest_shape(self):
        original = self.scene.vertices.copy()
        cameras = fit_front_cameras(self.scene, self.binding, self.evidence, self.seeds)['cameras']
        result = fit_temple_poses(self.scene, self.binding, self.evidence, cameras)
        for view_id, expected in self.states.items():
            actual = result['view_states'][view_id]
            self.assertAlmostEqual(actual.left_degrees, expected.left_degrees, places=4)
            self.assertAlmostEqual(actual.right_degrees, expected.right_degrees, places=4)
        assessment = assess_geometry_candidate(self.scene, self.binding, self.evidence, cameras, result['view_states'])
        self.assertLess(max(row['rms_px'] for row in assessment['groups']), 1e-4)
        np.testing.assert_array_equal(self.scene.vertices, original)

    def test_temple_observations_cannot_move_front_camera(self):
        changed = [dataclasses.replace(row, targets_xy=row.targets_xy + [70, -40])
                   if 'temple' in row.group_id else row for row in self.evidence]
        baseline = fit_front_cameras(self.scene, self.binding, self.evidence, self.seeds)['cameras']
        actual = fit_front_cameras(self.scene, self.binding, changed, self.seeds)['cameras']
        self.assertEqual(baseline, actual)

    def test_pose_preserves_source_triangle_lineage_and_front(self):
        posed = pose_scene(self.scene, self.binding, self.states['front'])
        np.testing.assert_array_equal(posed.source_face_ids, np.arange(len(self.scene.faces)))
        np.testing.assert_array_equal(posed.source_vertex_ids, self.scene.faces.ravel())
        front = np.asarray(self.binding.face_roles) == 'front'
        np.testing.assert_array_equal(posed.mesh.vertices[posed.mesh.faces[front]], self.scene.vertices[self.scene.faces[front]])
        self.assertFalse(np.allclose(posed.mesh.vertices[posed.mesh.faces[~front]], self.scene.vertices[self.scene.faces[~front]]))

    def test_shared_vertex_front_is_not_dragged_by_temple(self):
        mesh = TriangleMesh(np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., -1.]]),
                            np.array([[0, 1, 2], [0, 1, 3]]), [])
        binding = bind_parts(mesh, front_faces=[0], left_temple_faces=[1],
            hinges=[Hinge('left', np.zeros(3), np.array([0., 1., 0.]))], provenance='test')
        posed = pose_scene(mesh, binding, ViewState('test', 45.))
        np.testing.assert_array_equal(posed.mesh.vertices[:3], mesh.vertices[mesh.faces[0]])
        self.assertFalse(np.allclose(posed.mesh.vertices[4], mesh.vertices[1]))

    def test_normal_only_observation_ignores_tangent(self):
        row = next(row for row in self.evidence if row.group_id == 'left_temple')
        normal = np.tile([1., 0.], (len(row.face_ids), 1))
        original = dataclasses.replace(row, normal_xy=normal)
        shifted = dataclasses.replace(original, targets_xy=row.targets_xy + [0, 100])
        a = fit_temple_poses(self.scene, self.binding, [original], {'front': self.cameras['front']})
        b = fit_temple_poses(self.scene, self.binding, [shifted], {'front': self.cameras['front']})
        self.assertEqual(a['view_states'], b['view_states'])

    def test_binding_and_evidence_do_not_transfer_to_changed_geometry(self):
        moved = TriangleMesh(self.scene.vertices + .001, self.scene.faces, [])
        with self.assertRaisesRegex(ValueError, 'does not match'):
            pose_scene(moved, self.binding, self.states['front'])
        row = dataclasses.replace(self.evidence[0], source_geometry_sha256='a' * 64)
        with self.assertRaisesRegex(ValueError, 'do not match'):
            fit_front_cameras(self.scene, self.binding, [row], self.seeds)

    def test_invalid_face_overlap_and_bounds_rejected(self):
        with self.assertRaisesRegex(ValueError, 'disjoint'):
            bind_parts(self.scene, front_faces=[0], left_temple_faces=[0], provenance='test')
        with self.assertRaisesRegex(ValueError, 'outside'):
            pose_scene(self.scene, self.binding, ViewState('front', 120))
        with self.assertRaisesRegex(ValueError, 'inside'):
            dataclasses.replace(self.evidence[0], barycentric=np.full((12, 3), .5))

    def test_unobserved_arms_remain_explicit(self):
        rows = [row for row in self.evidence if 'temple' not in row.group_id]
        result = fit_temple_poses(self.scene, self.binding, rows, self.cameras)
        self.assertEqual(result['report']['front']['left']['status'], 'unobserved')
        self.assertEqual(result['view_states']['front'].left_degrees, 0.)

    def test_unbound_nonzero_angle_is_rejected(self):
        binding = bind_parts(self.scene, front_faces=range(len(self.scene.faces)), provenance='no articulated parts')
        with self.assertRaisesRegex(ValueError, 'bound hinge'):
            pose_scene(self.scene, binding, ViewState('front', 12.))

    def test_shared_shape_proposal_is_bounded_and_keeps_face_lineage(self):
        rows = []
        for row in self.evidence:
            if 'lens' not in row.group_id:
                continue
            points = self.scene.vertices[self.scene.faces[row.face_ids]].mean(axis=1).copy()
            if 'lens' in row.group_id:
                points[:, 1] *= 1.06
            rows.append(dataclasses.replace(row, targets_xy=project(points, self.cameras[row.view_id])))
        result = fit_shared_geometry(self.scene, self.binding, rows, self.cameras, self.states,
                                    policy=StructuredPolicy(maximum_shape_evaluations=20))
        self.assertTrue(result['report']['retained'])
        np.testing.assert_array_equal(result['scene'].faces, self.scene.faces)
        limit = np.ptp(self.scene.vertices, axis=0).max() * .052
        self.assertLess(np.linalg.norm(result['scene'].vertices - self.scene.vertices, axis=1).max(), limit)
        self.assertTrue(result['report']['every_articulated_group_nonregressed'])
        self.assertFalse(result['report']['hinge_contact_verified'])

    def test_no_names_to_verified_binding_shortcut(self):
        hypotheses = propose_part_bindings(self.scene, {})
        self.assertTrue(all(not binding.to_dict()['semantic_identity_verified'] for binding in hypotheses))
        partial = bind_parts(self.scene, front_faces=range(len(self.scene.faces)), provenance='deliberately absent arms')
        report = classify_geometry_failure({'groups': []}, binding=partial)
        self.assertEqual(report['class'], 'missing_part_binding_or_topology')

    def test_exact_shape_does_not_generate_unneeded_cage(self):
        result = fit_shared_geometry(self.scene, self.binding, self.evidence, self.cameras, self.states,
                                    policy=StructuredPolicy(maximum_shape_evaluations=3))
        self.assertFalse(result['report']['retained'])
        self.assertIsNone(result['field'])
        np.testing.assert_array_equal(result['scene'].vertices, self.scene.vertices)


if __name__ == '__main__':
    unittest.main()
