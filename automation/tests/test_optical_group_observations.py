"""Effective-group photo semantics and source-normalization preservation."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

from reconstruction.camera import Camera
from reconstruction.mesh import TriangleMesh
from reconstruction.optical_group_observations import project_effective_group_fields
from reconstruction.photo_lens_observations import project_optical_fields
from test_optical_group_raster import boxes


class EffectiveGroupPhotoProjectionTests(unittest.TestCase):
    def project(self, specifications, *, camera=None, transform=None):
        mesh, groups = boxes(specifications)
        uv = (mesh.vertices[:, :2]+.6)/1.2
        normals = np.tile([0., 0., 2.], (len(mesh.vertices), 1))
        normalization = {'center': [0, 0, 0], 'extent': 1}
        if transform:
            center, scale = transform
            mesh = TriangleMesh(mesh.vertices*scale+center, mesh.faces, [])
            normalization = {'center': center, 'extent': scale}
        camera = camera or Camera(0, 0, 0, 0, 20, 20, 20)
        args = (mesh, uv, normals, groups, camera, (41, 41), normalization)
        return project_effective_group_fields(*args), args

    def test_closed_front_and_back_are_one_event_while_sheet_profile_stays_distinct(self):
        specs = [(-.6, .6, -.6, .6, -.3, .3, 7)]
        for yaw in (0, 180):
            with self.subTest(yaw=yaw):
                fields, args = self.project(specs, camera=Camera(yaw, 0, 0, 0, 20, 20, 20))
                self.assertEqual(fields['group'][20, 20], 7)
                self.assertFalse(fields['stacked'][20, 20])
                self.assertAlmostEqual(fields['incidence'][20, 20], 0.)
                self.assertAlmostEqual(fields['v'][20, 20], .5)
                self.assertEqual(fields['rear_weight'][20, 20], 0.)
                np.testing.assert_allclose(fields['reflected'][20, 20], [0., 0., 1.], atol=1e-15)
                # The old profile must keep its two separate intersections.
                self.assertTrue(project_optical_fields(*args)['stacked'][20, 20])

    def test_opaque_stop_and_distinct_group_stacks_keep_different_evidence(self):
        fields, _ = self.project([(-.6, .6, -.6, .6, -.3, .5, 7),
                                  (-.6, .6, -.6, .6, -.1, 0, -1),
                                  (-.6, .6, -.6, .6, -.25, -.2, 8)])
        self.assertEqual(fields['group'][20, 20], 7)
        self.assertEqual(fields['rear_weight'][20, 20], 1.)
        self.assertFalse(fields['stacked'][20, 20])
        fields, _ = self.project([(-.6, .6, -.6, .6, .2, .3, 7),
                                  (-.6, .6, -.6, .6, -.1, 0, 8)])
        self.assertTrue(fields['visible'][20, 20])
        self.assertTrue(fields['stacked'][20, 20])
        self.assertEqual(fields['group'][20, 20], -1)
        self.assertTrue(np.isnan(fields['v'][20, 20]))
        fields, _ = self.project([(-.6, .6, -.6, .6, -.3, .3, 7),
                                  (-.6, .6, -.6, .6, .5, .6, -1)])
        self.assertFalse(fields['visible'][20, 20])
        self.assertTrue(np.isnan(fields['v'][20, 20]))

    def test_fixed_camera_normalization_preserves_fields_after_scale_and_translation(self):
        specs = [(-.6, .6, -.6, .6, -.3, .3, 7)]
        camera = Camera(23, 7, 31, .3, 20, 20, 20)
        base, _ = self.project(specs, camera=camera)
        changed, _ = self.project(specs, camera=camera, transform=([1., -2., 3.], 16.))
        np.testing.assert_array_equal(base['group'], changed['group'])
        for key in ('v', 'incidence', 'reflected', 'rear_weight'):
            np.testing.assert_allclose(base[key], changed[key], atol=1e-13, equal_nan=True)
        json.dumps(base['profile_diagnostics'], allow_nan=False)

    def test_undefined_normals_and_cross_group_ties_remain_unknown(self):
        specs = [(-.6, .6, -.6, .6, -.3, .3, 7)]
        fields, args = self.project(specs)
        normals = args[2].copy()
        normals[:] = 0
        invalid = project_effective_group_fields(args[0], args[1], normals, *args[3:])
        self.assertTrue(invalid['invalid_attributes'][20, 20])
        self.assertEqual(invalid['group'][20, 20], -1)
        tied, _ = self.project(specs+[(-.6, .6, -.6, .6, -.4, .3, 8)])
        self.assertTrue(tied['ambiguous'][20, 20])
        self.assertTrue(np.isnan(tied['incidence'][20, 20]))


class ExportedGroupObservationTests(unittest.TestCase):
    def setUp(self):
        from reconstruction.lens_asset import _pack_glb
        from reconstruction.lens_appearance import DensityKeyframe, LensAppearance
        from reconstruction.optical_groups import prepare_optical_group
        from reconstruction.optical_group_asset import write_optical_group_candidate
        from test_optical_groups import FRAME, IDENTITY

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.source, self.model = self.folder/'source.glb', self.folder/'candidate.glb'
        self.export_path, self.region_path = self.folder/'export.json', self.folder/'regions.json'
        # Two separate closed members of ONE supplied effective group. A third
        # unrelated primitive remains ordinary scene geometry.
        mesh, _ = boxes([(-.6, .6, -.6, .6, .2, .3, 0),
                         (-.6, .6, -.6, .6, -.3, -.2, 0),
                         (.8, .9, -.6, .6, -.1, .1, -1)])
        doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': 0}],
               'bufferViews': [], 'accessors': [], 'materials': [{}],
               'meshes': [], 'nodes': [], 'scenes': [{'nodes': [0, 1, 2]}], 'scene': 0}
        binary = bytearray()
        def append(array, kind, component):
            binary.extend(b'\0'*(-len(binary) % 4))
            doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes})
            binary.extend(array.tobytes())
            doc['accessors'].append({'bufferView': len(doc['bufferViews'])-1, 'componentType': component,
                                     'count': array.size if kind == 'SCALAR' else len(array), 'type': kind})
            return len(doc['accessors'])-1
        for i in range(3):
            p = mesh.vertices[8*i:8*i+8].astype('<f4')
            n = np.tile([0., 0., 2.], (8, 1)).astype('<f4')
            f = (mesh.faces[12*i:12*i+12]-8*i).astype('<u4')
            doc['meshes'].append({'primitives': [{'attributes': {'POSITION': append(p, 'VEC3', 5126),
                                                               'NORMAL': append(n, 'VEC3', 5126)},
                                                  'indices': append(f, 'SCALAR', 5125), 'material': 0}]})
            doc['nodes'].append({'mesh': i, 'name': f'source-{i}'})
        doc['buffers'][0]['byteLength'] = len(binary)
        self.source.write_bytes(_pack_glb(doc, bytes(binary)))
        self.sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
        from reconstruction.mesh import load_glb
        loaded = load_glb(self.source)
        members = []
        for i, part in enumerate(loaded.parts[:2]):
            v, f = part['vertex_start'], part['face_start']
            members.append({'id': f'member-{i}', 'source_binding': {
                'asset_sha256': self.sha(self.source), 'node_index': i, 'mesh_index': i,
                'primitive_index': 0, 'material_index': 0}, 'coordinate_frame_id': FRAME['id'],
                'positions': loaded.vertices[v:v+8], 'indices': loaded.faces[f:f+12]-v,
                'normals': np.tile([0., 0., 2.], (8, 1)), 'normal_transform': {
                    'method': 'identity', 'source_to_common_matrix': np.eye(4).tolist(),
                    'provenance': {'method': 'authored fixture NORMAL'}}})
        prepared = prepare_optical_group('physical-group', members, coordinate_frame=FRAME, identity=IDENTITY)
        exported = write_optical_group_candidate(self.source, self.model,
            [{'prepared': prepared, 'appearance': LensAppearance((DensityKeyframe(0., (.3, .4, .5)),))}],
            source_sha256=self.sha(self.source), provenance={'method': 'integration_fixture'})
        self.export_path.write_text(json.dumps(exported))
        self.regions = {'candidate_sha256': self.sha(self.source), 'photos': []}
        for i, (name, yaw) in enumerate((('front', 0), ('back', 180))):
            pixels = np.full((64, 64, 3), 245, np.uint8)
            pixels[8:57, 8:57] = [150+i, 120, 100]
            photo = self.folder/f'{name}.png'
            Image.fromarray(pixels).save(photo)
            folder = self.folder/name/'lens'
            folder.mkdir(parents=True)
            mask = np.zeros((64, 64), bool)
            mask[8:57, 8:57] = True
            mask_path = folder/'mask.png'
            Image.fromarray(mask.astype(np.uint8)*255).save(mask_path)
            self.regions['photos'].append({'id': name, 'source': str(photo), 'source_sha256': self.sha(photo),
                'image_size': [64, 64], 'candidate_projection': {
                    'status': 'candidate_conditioned_projection', 'candidate_sha256': self.sha(self.source),
                    'camera': Camera(yaw, 0, 0, 0, 40, 32, 32).to_dict(), 'working_size': [64, 64],
                    'normalization': {'center': [0, 0, 0], 'extent': 1}},
                'regions': [{'id': 'lens', 'directory': 'lens', 'kind': 'candidate_optical_region',
                    'prior': {'part_indices': [0, 1]}, 'hypotheses': [
                        {'index': j, 'intersection': {'path': 'mask.png', 'sha256': self.sha(mask_path),
                                                     'pixels': int(mask.sum())}} for j in range(3)]}]})
        self.region_path.write_text(json.dumps(self.regions))

    def build(self):
        from reconstruction.optical_group_observations import build_optical_group_observations
        return build_optical_group_observations(self.model, self.export_path, self.region_path)

    def test_actual_export_multipart_group_preserves_masks_and_feeds_joint_fitter(self):
        from reconstruction.joint_photo_lens_fit import JointPhotoLensFitPolicy, fit_joint_photo_lens_candidates
        from reconstruction.photo_lens_fit import PhotoLensFitPolicy
        result = self.build()
        self.assertEqual(len(result['groups']), 1)
        group = next(iter(result['groups'].values()))
        self.assertEqual(len(group['observations']), 6)  # Three masks per view, not per member.
        self.assertEqual(group['surface_binding']['prepared_glb_sha256'], self.sha(self.model))
        for obs in group['observations']:
            self.assertEqual(obs['provenance']['coverage']['source_part_indices'], [0, 1])
            self.assertTrue(np.isfinite(obs['intrinsic_v']).all())
            self.assertTrue(np.all(obs['incidence_degrees'] < 1e-8))
            self.assertEqual(obs['provenance']['coverage']['stacked_pixels'], 0)
        fitted = fit_joint_photo_lens_candidates([group], policy=JointPhotoLensFitPolicy(
            photo_policy=PhotoLensFitPolicy(families=('uniform_tint',), lighting_families=('constant',),
                                            roughness_values=(.05,), max_nfev=5, minimum_validation_points_per_photo=1)))
        self.assertEqual(fitted['exploration']['mask_branches'], 1)
        self.assertEqual(fitted['exploration']['optimization_runs'], 3)
        self.assertFalse(result['report']['accepted'])
        json.dumps(result['report'], allow_nan=False)

    def test_group_photo_exclusions_drop_only_the_named_view_and_stay_in_the_ledger(self):
        from reconstruction.optical_group_observations import build_optical_group_observations
        group = next(iter(self.build()['groups'].values()))
        gid = group['surface_binding']['material_group_id']
        photos = sorted({o['photo_id'] for o in group['observations']})
        self.assertEqual(len(photos), 2)
        exclusions = [{'group_id': gid, 'photo_id': photos[1], 'reason': 'registration: inside share 0.683 under 0.8'}]
        result = build_optical_group_observations(self.model, self.export_path, self.region_path, group_photo_exclusions=exclusions)
        kept = next(iter(result['groups'].values()))['observations']
        self.assertEqual({o['photo_id'] for o in kept}, {photos[0]})
        self.assertEqual(len(kept), 3, 'the other photo keeps its three mask hypotheses')
        dropped = result['report']['registration_excluded_observations']
        self.assertEqual(len(dropped), 3)
        self.assertTrue(all(d['group_id'] == gid and d['photo_id'] == photos[1] and d['reason'].startswith('registration') for d in dropped))
        self.assertEqual(result['report']['group_photo_exclusions'], exclusions)
        self.assertEqual(result['report']['groups'][0]['observations'], 3)
        untouched = build_optical_group_observations(self.model, self.export_path, self.region_path,
                                                     group_photo_exclusions=[{'group_id': 'group-none', 'photo_id': photos[1], 'reason': 'x'}])
        self.assertEqual(len(next(iter(untouched['groups'].values()))['observations']), 6, 'an exclusion for an unknown group drops nothing')
        for bad in ([{'group_id': gid, 'photo_id': photos[1]}], [{'group_id': gid, 'photo_id': photos[1], 'reason': ''}],
                    [exclusions[0], dict(exclusions[0])], {'group_id': gid}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                build_optical_group_observations(self.model, self.export_path, self.region_path, group_photo_exclusions=bad)
        json.dumps(result['report'], allow_nan=False)

    def test_unbound_or_duplicate_region_parts_and_changed_source_lineage_rejected(self):
        for parts in ([0, 0], [], [-1]):
            self.regions['photos'][0]['regions'][0]['prior']['part_indices'] = parts
            self.region_path.write_text(json.dumps(self.regions))
            with self.subTest(parts=parts), self.assertRaisesRegex(ValueError, 'unique nonnegative'):
                self.build()
        # A prior touching an undeclared piece is retained with that piece
        # recorded as unbound; the declared group still receives its samples.
        self.regions['photos'][0]['regions'][0]['prior']['part_indices'] = [0, 2]
        self.region_path.write_text(json.dumps(self.regions))
        result = self.build()
        self.assertEqual(result['report']['unbound_region_prior_parts'],
                         [{'prior_part_indices': [0, 2], 'unbound_part_indices': [2]}])
        self.assertTrue(any(group['observations'] for group in result['groups'].values()))
        self.regions['candidate_sha256'] = '0'*64
        self.region_path.write_text(json.dumps(self.regions))
        with self.assertRaisesRegex(ValueError, 'different candidates'):
            self.build()


if __name__ == '__main__':
    unittest.main()
