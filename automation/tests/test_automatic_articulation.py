"""Automatic arm discovery, pose recovery and shared downstream view contracts."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.automatic_articulation import infer_automatic_part_bindings, fit_photo_arm_states, arm_correspondence_quality
from reconstruction.camera import Camera, render_mask
from reconstruction.lens_asset import _pack_glb
from reconstruction.mesh import TriangleMesh, load_glb
from reconstruction.view_scene import (ViewState, pose_scene, make_view_scene_contract,
    pose_mesh_from_contract, read_view_scene_contract, transfer_face_roles)
from reconstruction.component_projection import project_components
from reconstruction.observations import observe_image
from reconstruction.optical_group_observations import project_effective_group_fields


def fixture():
    boxes = [((-.5, -.13, -.035), (.5, .13, .035)),
             ((-.48, .04, -.55), (-.44, .08, -.035)),
             ((-.48, .04, -.90), (-.44, .08, -.551)),
             ((.44, .04, -.90), (.48, .08, -.035))]
    vertices, faces, parts = [], [], []
    template = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5],
                         [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                         [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    for i, (lo, hi) in enumerate(boxes):
        p = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        vertices.append(p); faces.append(template + 8 * i)
        parts.append({'face_start': 12 * i, 'face_count': 12, 'vertex_start': 8 * i, 'vertex_count': 8,
                      'name': 'misleading lens name', 'material': 'not a role', 'transmission': 0})
    return TriangleMesh(np.concatenate(vertices), np.concatenate(faces), parts)


def write_fixture(path):
    scene = fixture(); binary = bytearray(); views = []; accessors = []; meshes = []
    for part in scene.parts:
        p = np.asarray(scene.vertices[part['vertex_start']:part['vertex_start'] + part['vertex_count']], '<f4')
        f = np.asarray(scene.faces[part['face_start']:part['face_start'] + part['face_count']] - part['vertex_start'], '<u4').ravel()
        ids = []
        for array, kind, width in ((p, 5126, 'VEC3'), (f, 5125, 'SCALAR')):
            views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes}); binary.extend(array.tobytes())
            accessors.append({'bufferView': len(views) - 1, 'componentType': kind, 'type': width, 'count': len(array)})
            ids.append(len(accessors) - 1)
        meshes.append({'primitives': [{'attributes': {'POSITION': ids[0]}, 'indices': ids[1]}]})
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}], 'bufferViews': views,
           'accessors': accessors, 'meshes': meshes, 'nodes': [{'mesh': i} for i in range(len(meshes))],
           'scenes': [{'nodes': list(range(len(meshes)))}], 'scene': 0}
    Path(path).write_bytes(_pack_glb(doc, binary))
    return load_glb(path)


class AutomaticArticulationTests(unittest.TestCase):
    def test_geometry_discovers_fragmented_arm_without_names(self):
        mesh = fixture(); result = infer_automatic_part_bindings(mesh)
        self.assertEqual(result['report']['status'], 'automatic_unverified_binding', result['report'])
        binding = result['bindings'][0]
        self.assertEqual(set(binding.face_roles[:12]), {'front'})
        self.assertEqual(set(binding.face_roles[12:36]), {'left_temple'})
        self.assertEqual(set(binding.face_roles[36:]), {'right_temple'})
        self.assertFalse(binding.to_dict()['semantic_identity_verified'])

    def test_front_only_and_detached_arms_abstain(self):
        mesh = fixture()
        front = TriangleMesh(mesh.vertices[:8], mesh.faces[:12], [mesh.parts[0]])
        self.assertFalse(infer_automatic_part_bindings(front)['bindings'])
        disconnected = fixture(); disconnected.vertices[8:, 2] -= 2.
        self.assertFalse(infer_automatic_part_bindings(disconnected)['bindings'])

    def test_photo_pose_recovery_improves_held_out_arm_boundaries(self):
        mesh = fixture(); binding = infer_automatic_part_bindings(mesh)['bindings'][0]
        camera = Camera(45., 10., 0., 0., 150., 140., 100.)
        truth = ViewState('angled', 22., -18.)
        mask = render_mask(pose_scene(mesh, binding, truth).mesh, camera, (240, 280))
        rgb = np.full((240, 280, 3), 255, np.uint8); rgb[mask] = 30
        result = fit_photo_arm_states(mesh, binding, rgb, camera, 'angled')
        retained = [row for row in result['report'].values() if row.get('retained')]
        self.assertTrue(retained, result['report'])
        self.assertTrue(all(r['proposal']['holdout_error_px'] < r['baseline']['holdout_error_px'] for r in retained))
        supported = [(side, row) for side, row in result['report'].items() if row.get('retained')]
        for side, row in supported:
            self.assertLess(abs(getattr(result['state'], side + '_degrees') - getattr(truth, side + '_degrees')), 5.)

    def test_blank_photo_cannot_create_articulation(self):
        mesh = fixture(); binding = infer_automatic_part_bindings(mesh)['bindings'][0]
        result = fit_photo_arm_states(mesh, binding, np.full((200, 260, 3), 255, np.uint8),
                                     Camera(45., 0., 0., 0., 140., 130., 100.), 'blank')
        self.assertEqual(result['state'], ViewState('blank'))

    def test_large_relative_gain_still_requires_good_absolute_correspondence(self):
        # Real failure class: a ten-to-three-pixel gain is attractive but still
        # follows studio reflection structure instead of the photographed arm.
        measurement={'supported':True,'holdout_error_px':3.,'holdout_near_edge_fraction':.5}
        self.assertFalse(arm_correspondence_quality(measurement,100.)['supported'])
        measurement.update(holdout_error_px=.8,holdout_near_edge_fraction=.95)
        self.assertTrue(arm_correspondence_quality(measurement,100.)['supported'])
        measurement['holdout_error_px']*=2
        self.assertTrue(arm_correspondence_quality(measurement,200.)['supported'])

    def test_wrong_camera_does_not_become_a_confident_arm_pose(self):
        mesh=fixture();binding=infer_automatic_part_bindings(mesh)['bindings'][0]
        wrong=Camera(45.,0.,0.,0.,140.,130.,95.)
        actual=Camera(45.,0.,0.,0.,140.,130.,104.)
        mask=render_mask(pose_scene(mesh,binding,ViewState('photo',20.,-15.)).mesh,actual,(220,280))
        rgb=np.full((220,280,3),255,np.uint8);rgb[mask]=20
        result=fit_photo_arm_states(mesh,binding,rgb,wrong,'photo')
        self.assertEqual(result['state'],ViewState('photo'))
        self.assertIn('camera_or_arm_ambiguous',[r['status'] for r in result['report'].values()])

    def test_contract_pose_matches_component_projection_and_reordered_triangles(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.glb'; mesh = write_fixture(path)
            binding = infer_automatic_part_bindings(mesh)['bindings'][0]
            state = ViewState('photo', 20., -15.)
            contract = make_view_scene_contract(mesh, binding, {'photo': state}, source_model=path,
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), photo_sha256={'photo': 'b' * 64}, evidence={})
            context = {'contract': contract, 'view_id': 'photo', 'source_image_sha256': 'b' * 64}
            camera = Camera(35., 0., 0., 0., 140., 130., 100.)
            expected = pose_scene(mesh, binding, state).mesh
            projection = project_components(mesh, np.zeros(len(mesh.faces), int), camera, (200, 260), view_scene=context)
            actual = project_components(expected, np.zeros(len(mesh.faces), int), camera, (200, 260))
            np.testing.assert_array_equal(projection['full_scene'].mask, actual['full_scene'].mask)
            np.testing.assert_allclose(projection['full_scene'].depth, actual['full_scene'].depth)
            reversed_mesh = TriangleMesh(mesh.vertices, mesh.faces[::-1], [])
            result = pose_mesh_from_contract(reversed_mesh, contract, 'photo', photo_sha256='b' * 64)
            np.testing.assert_allclose(result['mesh'].vertices[result['mesh'].faces], expected.vertices[expected.faces][::-1])
            normalized = TriangleMesh((mesh.vertices - [.1, .2, .3]) / 2., mesh.faces, mesh.parts)
            posed = pose_mesh_from_contract(normalized, contract, 'photo', normalization={'center': [.1, .2, .3], 'extent': 2.})
            np.testing.assert_allclose(posed['mesh'].vertices[posed['mesh'].faces] * 2 + [.1, .2, .3], expected.vertices[expected.faces])
            bad = deepcopy(contract); bad['views']['photo']['left_degrees'] = 40.
            with self.assertRaisesRegex(ValueError, 'hash'):
                read_view_scene_contract(bad)
            with self.assertRaisesRegex(ValueError, 'photograph'):
                pose_mesh_from_contract(mesh, contract, 'photo', photo_sha256='c' * 64)

    def test_alpha_geometry_ignores_hidden_rgb_and_does_not_claim_transmission(self):
        pixels = np.random.default_rng(3).integers(0, 256, (200, 300, 4), dtype=np.uint8)
        pixels[..., 3] = 0; pixels[70:120, 50:250, 3] = 255
        a = observe_image(pixels); pixels[pixels[..., 3] == 0, :3] = 0; b = observe_image(pixels)
        self.assertEqual(a.status, 'measured')
        np.testing.assert_array_equal(a.mask, b.mask)
        self.assertFalse(a.metrics['physical_lens_transmission_inferred'])
        self.assertFalse(a.metrics['photometric_background_known'])

    def test_actual_optical_ray_fields_use_the_same_posed_arm_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.glb'; mesh = write_fixture(path)
            binding = infer_automatic_part_bindings(mesh)['bindings'][0]; state = ViewState('photo', 25., -20.)
            contract = make_view_scene_contract(mesh, binding, {'photo': state}, source_model=path,
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), photo_sha256={'photo': 'b' * 64}, evidence={})
            uv = np.column_stack((np.full(len(mesh.vertices), .5), np.linspace(0., 1., len(mesh.vertices))))
            normals = np.tile([0., 0., 1.], (len(mesh.vertices), 1))
            groups = np.full(len(mesh.faces), -1, np.int64); groups[:12] = 0
            context = {'contract': contract, 'view_id': 'photo', 'source_image_sha256': 'b' * 64}
            posed = pose_mesh_from_contract(mesh, contract, 'photo', uv=uv, normals=normals)
            camera = Camera(40., 8., 0., 0., 130., 130., 100.); normalization = {'center': [0., 0., 0.], 'extent': 1.}
            actual = project_effective_group_fields(mesh, uv, normals, groups, camera, (200, 260), normalization, view_scene=context)
            expected = project_effective_group_fields(posed['mesh'], posed['uv'], posed['normals'], groups, camera, (200, 260), normalization)
            for name in ('group', 'visible', 'rear_weight', 'v', 'incidence'):
                np.testing.assert_allclose(actual[name], expected[name], equal_nan=True)
            self.assertEqual(actual['profile_diagnostics']['articulation']['contract_sha256'], contract['contract_sha256'])

    def test_default_refinement_runs_automatic_pose_and_persists_contract(self):
        from reconstruction.refine_photos import run, PhotoInput
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / 'source.glb'; mesh = write_fixture(path)
            binding = infer_automatic_part_bindings(mesh)['bindings'][0]
            photos = []
            for i, yaw in enumerate((40., -40.)):
                state = ViewState(f'photo-{i}', 15. + 5 * i, -15.)
                camera = Camera(yaw, 12., 0., 0., 120., 160., 95.)
                mask = render_mask(pose_scene(mesh, binding, state).mesh, camera, (220, 320))
                rgb = np.full((220, 320, 3), 255, np.uint8); rgb[mask] = 25
                image = root / f'{i}.png'; Image.fromarray(rgb).save(image)
                photos.append(PhotoInput(f'photo-{i}', image, 'angled'))
            original = path.read_bytes()
            report = run(path, photos, root / 'run', resolution=160, camera_evaluations=30)
            self.assertIn('view_scene', report)
            self.assertTrue(report['settings']['automatic_articulation'])
            self.assertEqual(len(report['view_scene']['views']), 2)
            self.assertTrue(all(v.get('camera_fit') for v in report['views']))
            self.assertEqual(path.read_bytes(), original)
            read_view_scene_contract(report['view_scene'])

    def test_partition_reordering_preserves_exact_source_arm_lineage(self):
        from reconstruction.partition_glb import partition_glb_bytes
        from reconstruction.candidate_cameras import transfer_cameras_to_partition
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source.glb'; mesh = write_fixture(source)
            raw = source.read_bytes(); sha = hashlib.sha256(raw).hexdigest()
            binding = infer_automatic_part_bindings(mesh)['bindings'][0]; state = ViewState('photo', 25., -20.)
            contract = make_view_scene_contract(mesh, binding, {'photo': state}, source_model=source,
                source_sha256=sha, photo_sha256={'photo': 'b' * 64}, evidence={})
            output, receipt = partition_glb_bytes(raw, {'schema_version': 1, 'source_sha256': sha,
                'provenance': {'method': 'test'}, 'partitions': [{
                    'source_binding': {'node_index': 1, 'mesh_index': 1, 'primitive_index': 0},
                    'pieces': [{'id': 'last-first', 'source_face_indices': list(range(6, 12))},
                               {'id': 'first-last', 'source_face_indices': list(range(6))}]}]})
            target = root / 'partition.glb'; target.write_bytes(output)
            refinement = {'source_sha256': sha, 'view_scene': contract,
                'normalization': {'center': [0., 0., 0.], 'extent': 1.},
                'views': [{'view_id': 'photo', 'camera_fit': {'camera': {}}}]}
            result = transfer_cameras_to_partition(refinement, raw, output, receipt, partitioned_model_path=str(target))
            rebound_mesh, rebound, rebound_state = read_view_scene_contract(result['view_scene'], view_id='photo')
            self.assertEqual(rebound_state, state)
            self.assertEqual(result['view_scene_transfer']['unmatched_faces'], 0)
            for part in rebound_mesh.parts:
                rows = rebound.face_roles[part['face_start']:part['face_start'] + part['face_count']]
                points = rebound_mesh.vertices[rebound_mesh.faces[part['face_start']:part['face_start'] + part['face_count']]]
                expected = 'front' if np.ptp(points[..., 0]) > .8 else ('right_temple' if np.mean(points[..., 0]) > 0 else 'left_temple')
                self.assertEqual(set(rows), {expected})


if __name__ == '__main__':
    unittest.main()
