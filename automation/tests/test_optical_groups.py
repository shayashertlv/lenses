"""Source preservation, shared coordinates, provenance and effective normals."""
from copy import deepcopy
import json
import unittest

import numpy as np

from reconstruction.optical_groups import faceforward_normals, prepare_optical_group


FRAME = {'id': 'source-world', 'units': 'meters', 'up_axis': '+Y', 'forward_axis': '+Z',
         'provenance': {'method': 'fixture baked scene transform'}}
IDENTITY = {'status': 'unverified', 'provenance': {'method': 'explicit diagnostic membership'}}


def primitive(identifier='first', y=0., node=0, normals=True):
    result = {'id': identifier, 'source_binding': {'asset_sha256': 'a'*64, 'node_index': node,
               'mesh_index': node, 'primitive_index': 0, 'material_index': None},
              'coordinate_frame_id': 'source-world',
              'positions': np.array([[0., y, 0.], [1., y, .1], [0., y+1., 0.]], np.float64),
              'indices': np.array([[0, 1, 2]], np.int64)}
    if normals:
        direction = np.array([-.1, 0., 1.]); direction /= np.linalg.norm(direction)
        result['normals'] = np.tile(direction, (3, 1))
        result['normal_transform'] = {'method': 'identity', 'source_to_common_matrix': np.eye(4).tolist(),
                                      'provenance': {'source': 'authored fixture normals'}}
    return result


def prepare(items, **kwargs):
    return prepare_optical_group('lens-group', items, coordinate_frame=deepcopy(FRAME),
                                 identity=deepcopy(IDENTITY), **kwargs)


class OpticalGroupTests(unittest.TestCase):
    def assert_prepared(self, result):
        self.assertEqual(result['report']['status'], 'prepared_candidate', result['report'])
        self.assertFalse(result['report']['accepted'])
        self.assertEqual(result['report']['runtime_compatibility'], 'unmeasured')
        json.dumps(result['report'], allow_nan=False)

    def test_source_arrays_order_and_normals_preserved_without_input_mutation(self):
        item = primitive(); before = deepcopy(item)
        result = prepare([item]); self.assert_prepared(result)
        output = result['primitives'][0]
        for key in ('positions', 'indices', 'normals'):
            np.testing.assert_array_equal(output[key], item[key])
            np.testing.assert_array_equal(item[key], before[key])
            self.assertFalse(np.shares_memory(output[key], item[key]))
        self.assertEqual(output['positions'].dtype, np.float64)
        self.assertEqual(output['indices'].dtype, np.int64)
        self.assertEqual(result['report']['primitives'][0]['source_binding'], item['source_binding'])
        self.assertIn('float32 export verification pending', result['report']['quantization'])

    def test_common_height_across_disjoint_members_is_order_invariant(self):
        lower, upper = primitive('lower', 0., 0), primitive('upper', 3., 1)
        first, second = prepare([lower, upper]), prepare([upper, lower])
        self.assert_prepared(first); self.assert_prepared(second)
        self.assertEqual(first['report']['group_sha256'], second['report']['group_sha256'])
        maps = [{p['id']: p for p in r['primitives']} for r in (first, second)]
        np.testing.assert_array_equal(maps[0]['lower']['uv'][:, 1], [0., 0., .25])
        np.testing.assert_array_equal(maps[0]['upper']['uv'][:, 1], [.75, .75, 1.])
        for key in maps[0]:
            np.testing.assert_array_equal(maps[0][key]['uv'], maps[1][key]['uv'])
        self.assertEqual(first['report']['topology_diagnostics']['connected_components'], 2)

    def test_unused_vertices_preserved_but_do_not_set_group_extent(self):
        item = primitive(normals=False)
        item['positions'] = np.vstack([item['positions'], [1e6, 1e6, -1e6]])
        result = prepare([item]); self.assert_prepared(result)
        output = result['primitives'][0]
        np.testing.assert_array_equal(output['positions'], item['positions'])
        np.testing.assert_array_equal(output['uv'][3], [.5, .5])
        np.testing.assert_array_equal(output['normals'][3], [0., 0., 1.])
        self.assertEqual(result['report']['referenced_bounds']['extent'][1], 1.)
        self.assertEqual(result['report']['counts']['unused_vertex_count'], 1)

    def test_missing_invalid_normals_are_explicit_derived_hypotheses(self):
        for value in (None, np.zeros((3, 3)), np.full((3, 3), np.nan)):
            item = primitive()
            if value is None:
                item.pop('normals'); item.pop('normal_transform')
            else:
                item['normals'] = value
            result = prepare([item]); self.assert_prepared(result)
            self.assertIn('hypothesis', result['report']['primitives'][0]['normal_policy'])
            normal = np.cross(item['positions'][1]-item['positions'][0], item['positions'][2]-item['positions'][0])
            normal /= np.linalg.norm(normal)
            np.testing.assert_allclose(result['primitives'][0]['normals'], np.tile(normal, (3, 1)))

    def test_invalid_unused_normals_do_not_discard_referenced_authored_directions(self):
        for unused in ([0., 0., 0.], [np.nan, 0., 0.]):
            item = primitive()
            # Deliberately artist-authored directions distinct from geometric
            # normals: a fallback would visibly change the referenced surface.
            item['normals'][:] = [0., 1., 1.]
            item['positions'] = np.vstack([item['positions'], [9., 9., 9.]])
            item['normals'] = np.vstack([item['normals'], unused])
            before = item['normals'][:3].copy()
            result = prepare([item]); self.assert_prepared(result)
            np.testing.assert_array_equal(result['primitives'][0]['normals'][:3], before)
            np.testing.assert_array_equal(result['primitives'][0]['normals'][3], [0., 0., 1.])
            row = result['report']['primitives'][0]
            self.assertIn('preserved', row['normal_policy'])
            self.assertEqual(row['invalid_unused_authored_normals_replaced'], 1)

    def test_normal_reversal_is_preserved_and_symmetric_faceforward_is_explicit(self):
        item = primitive(); item['normals'] *= -2
        result = prepare([item]); self.assert_prepared(result)
        np.testing.assert_array_equal(result['primitives'][0]['normals'], item['normals'])
        self.assertEqual(result['report']['primitives'][0]['normal_orientation']['corner_directions_opposing_winding'], 3)
        view = np.array([.2, .3, 1.])
        first = faceforward_normals(item['normals'], view)
        second = faceforward_normals(-item['normals'], view)
        np.testing.assert_allclose(first, second)
        self.assertTrue((first@view > 0).all())
        np.testing.assert_allclose(np.linalg.norm(first, axis=1), 1.)
        with self.assertRaises(ValueError):
            faceforward_normals(item['normals'], [0., 0., 0.])

    def test_closed_box_back_and_side_faces_are_retained(self):
        item = primitive(normals=False)
        item['positions'] = np.array([[x, y, z] for z in (0., 1.) for y in (0., 1.) for x in (0., 1.)])
        item['indices'] = np.array([[0, 1, 2], [1, 3, 2], [4, 6, 5], [5, 6, 7], [0, 4, 1], [1, 4, 5],
                                     [2, 3, 6], [3, 7, 6], [0, 2, 4], [2, 6, 4], [1, 5, 3], [3, 5, 7]])
        result = prepare([item]); self.assert_prepared(result)
        np.testing.assert_array_equal(result['primitives'][0]['indices'], item['indices'])
        self.assertEqual(result['report']['topology_diagnostics']['boundary_edges'], 0)
        self.assertEqual(result['report']['primitives'][0]['normal_orientation']['zero_xy_area_triangles'], 8)

    def test_bad_geometry_retains_diagnostic_reasons_without_usable_arrays(self):
        for kind in ('nonfinite', 'degenerate', 'cancelled'):
            item = primitive(normals=False)
            if kind == 'nonfinite': item['positions'][0, 0] = np.inf
            if kind == 'degenerate': item['positions'][2] = item['positions'][1]
            if kind == 'cancelled': item['indices'] = np.array([[0, 1, 2], [0, 2, 1]])
            result = prepare([item])
            self.assertEqual(result['report']['status'], 'unsupported')
            self.assertTrue(result['report']['reasons']); self.assertEqual(result['primitives'], [])
            self.assertEqual(result['report']['primitives'][0]['source_binding'], item['source_binding'])
            json.dumps(result['report'], allow_nan=False)

    def test_zero_height_rejected_and_zero_width_has_explicit_u_half(self):
        item = primitive(normals=False); item['positions'] = np.array([[0., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
        result = prepare([item]); self.assert_prepared(result)
        self.assertTrue(result['report']['zero_width_u_policy_applied'])
        np.testing.assert_array_equal(result['primitives'][0]['uv'][:, 0], .5)
        item['positions'] = np.array([[0., 0., 0.], [1., 0., 0.], [0., 0., 1.]])
        result = prepare([item])
        self.assertEqual(result['report']['status'], 'unsupported')

    def test_identity_claim_never_grants_acceptance(self):
        with self.assertRaisesRegex(ValueError, 'Unverified'):
            prepare([primitive()], require_verified_identity=True)
        result = prepare_optical_group('lens-group', [primitive()], coordinate_frame=deepcopy(FRAME),
            identity={'status': 'externally_verified', 'provenance': {'claim': 'external reviewer'}}, require_verified_identity=True)
        self.assert_prepared(result)
        self.assertFalse(result['report']['accepted'])

    def test_frames_bindings_and_normal_transform_provenance_are_required(self):
        item = primitive(); item['coordinate_frame_id'] = 'other-frame'
        with self.assertRaisesRegex(ValueError, 'coordinate frame'):
            prepare([item])
        with self.assertRaisesRegex(ValueError, 'Duplicate source'):
            other = primitive('other'); prepare([primitive(), other])
        item = primitive(); item.pop('normal_transform')
        with self.assertRaisesRegex(ValueError, 'provenance'):
            prepare([item])
        item = primitive(); item['normal_transform']['source_to_common_matrix'][0][0] = 2.
        with self.assertRaisesRegex(ValueError, 'Identity'):
            prepare([item])
        item['normal_transform']['method'] = 'inverse_transpose_normalized'
        result = prepare([item]); self.assert_prepared(result)
        self.assertEqual(result['report']['primitives'][0]['normal_transform']['inverse_transpose_matrix'][0][0], .5)


if __name__ == '__main__':
    unittest.main()
