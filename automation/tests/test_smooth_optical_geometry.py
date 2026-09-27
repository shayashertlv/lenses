"""Smooth optical fields recover controlled bends and refuse incompatible shapes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from reconstruction.optical_groups import prepare_optical_group
from reconstruction.smooth_optical_geometry import (SmoothOpticalPolicy, evaluate_front_bend,
    fit_front_bend, propose_smooth_optical_group, run_smooth_optical_preparation)
from test_optical_groups import FRAME, IDENTITY
from test_deform_glb import fixture
from test_prepare_optical_groups import declarations
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from reconstruction.lens_asset import _pack_glb
from reconstruction.mesh import load_glb


def sheet(degree=2, height=0., identifier='sheet', n=35):
    x, y = np.meshgrid(np.linspace(-.5, .5, n), np.linspace(-.25, .25, n))
    z = .08 * x + .03 * y + (-(x ** 2) * .32 - y ** 2 * .1 if degree == 2 else 0) + height
    p = np.column_stack((x.ravel(), y.ravel(), z.ravel()))
    a = np.arange(n * n).reshape(n, n)[:-1, :-1].ravel()
    f = np.concatenate((np.column_stack((a, a + 1, a + n)),
                        np.column_stack((a + 1, a + n + 1, a + n))))
    return {'id': identifier, 'positions': p, 'indices': f.astype(np.int64),
        'source_binding': {'asset_sha256': 'a' * 64, 'node_index': 0 if identifier == 'sheet' else 1,
                           'mesh_index': 0 if identifier == 'sheet' else 1, 'primitive_index': 0},
        'coordinate_frame_id': 'source-world'}


class SmoothOpticalGeometryTests(unittest.TestCase):
    def test_quadratic_recovers_geometric_normals_without_using_authored_shading(self):
        item = sheet()
        result = fit_front_bend([item])
        self.assertEqual(result['report']['status'], 'supported_smooth_surface_hypothesis', result)
        self.assertEqual(result['fit']['degree'], 2)
        p = item['positions']
        expected = np.column_stack((-.08 + .64 * p[:, 0], -.03 + .2 * p[:, 1], np.ones(len(p))))
        expected /= np.linalg.norm(expected, axis=1)[:, None]
        np.testing.assert_allclose(evaluate_front_bend(result['fit'], p)[1], expected, atol=.004)
        self.assertLess(result['report']['selected_holdout']['holdout_p95_fraction'], .0003)

    def test_flat_surface_uses_least_complex_bend(self):
        result = fit_front_bend([sheet(1)])
        self.assertEqual(result['fit']['degree'], 1)
        self.assertLess(result['report']['selected_holdout']['holdout_rms_fraction'], 1e-12)

    def test_thin_closed_surfaces_share_effective_direction(self):
        front, back = sheet(height=.001), sheet(height=-.001, identifier='back')
        back['indices'] = back['indices'][:, ::-1]
        result = fit_front_bend([front, back])
        self.assertIsNotNone(result['fit'], result['report'])
        a = evaluate_front_bend(result['fit'], front['positions'])[1]
        b = evaluate_front_bend(result['fit'], back['positions'])[1]
        np.testing.assert_array_equal(a, b)

    def test_separated_layers_are_not_smoothed_into_one_lens(self):
        result = fit_front_bend([sheet(height=.08), sheet(height=-.08, identifier='back')])
        self.assertIsNone(result['fit'])
        self.assertTrue(set(result['report']['reasons']) & {'thick_or_multiple_depth_layers', 'smooth_surface_not_supported_on_holdout'})

    def test_shell_thickness_is_not_mistaken_for_surface_roughness(self):
        front, back = sheet(height=.02), sheet(height=-.02, identifier='back')
        back['indices'] = back['indices'][:, ::-1]
        result = fit_front_bend([front, back])
        self.assertEqual(result['fit']['representation'], 'two_skin_mid_surface', result['report'])
        self.assertGreater(result['report']['two_skin_support']['projected_support_overlap'], .95)
        self.assertLess(result['report']['two_skin_support']['normal_disagreement_p95_degrees'], .01)
        p = front['positions'].copy(); p[:, 2] -= .02
        np.testing.assert_allclose(evaluate_front_bend(result['fit'], p)[0], p[:, 2], atol=.0002)

    def test_incompatible_skin_curvatures_do_not_create_a_fake_mid_surface(self):
        front, back = sheet(height=.02), sheet(height=-.02, identifier='back')
        back['indices'] = back['indices'][:, ::-1]
        back['positions'][:, 2] -= .8 * back['positions'][:, 0] ** 2
        result = fit_front_bend([front, back])
        self.assertIsNone(result['fit'])
        self.assertEqual(result['report']['two_skin_attempt']['status'], 'incompatible_skins')

    def test_skin_overlap_uses_triangles_despite_different_tessellation(self):
        front = sheet(height=.02, n=35)
        back = sheet(height=-.02, n=26, identifier='back')
        back['indices'] = back['indices'][:, ::-1]
        result = fit_front_bend([front, back])
        self.assertEqual(result['fit']['representation'], 'two_skin_mid_surface')
        self.assertGreater(result['report']['two_skin_support']['projected_support_overlap'], .95)

    def test_insufficient_and_non_graph_geometry_remains_unsupported(self):
        result = fit_front_bend([sheet(n=3)])
        self.assertIsNone(result['fit'])
        self.assertIn('insufficient_spatial_support', result['report']['reasons'])
        item = sheet(); item['positions'] = item['positions'][:, [2, 1, 0]]
        result = fit_front_bend([item])
        self.assertIsNone(result['fit'])

    def test_non_smooth_geometry_does_not_pass_low_order_holdout(self):
        item = sheet()
        item['positions'][:, 2] += .04 * np.sin(item['positions'][:, 0] * 40)
        result = fit_front_bend([item])
        self.assertIsNone(result['fit'])
        self.assertIn('smooth_surface_not_supported_on_holdout', result['report']['reasons'])

    def test_output_geometry_uvs_and_input_are_unchanged(self):
        item = sheet()
        prepared = prepare_optical_group('lens', [item], identity=IDENTITY, coordinate_frame=FRAME)
        before = deepcopy(prepared)
        result = propose_smooth_optical_group(prepared)
        self.assertTrue(result['changed'])
        self.assertFalse(result['report']['accepted'])
        for field in ('positions', 'indices', 'uv'):
            np.testing.assert_array_equal(result['prepared']['primitives'][0][field], before['primitives'][0][field])
        for field in ('positions', 'indices', 'uv', 'normals'):
            np.testing.assert_array_equal(prepared['primitives'][0][field], before['primitives'][0][field])
        self.assertNotEqual(result['prepared']['report']['group_sha256'], prepared['report']['group_sha256'])

    def test_membership_order_does_not_affect_bend(self):
        a, b = sheet(height=.001), sheet(height=-.001, identifier='back')
        first, second = fit_front_bend([a, b]), fit_front_bend([b, a])
        np.testing.assert_allclose(first['fit']['coefficients'], second['fit']['coefficients'], atol=1e-10)

    def test_invalid_policy_and_geometry_rejected(self):
        for args in ({'grid_size': 4}, {'maximum_degrees': 4}, {'maximum_holdout_p95_fraction': float('nan')}):
            with self.assertRaises(ValueError):
                SmoothOpticalPolicy(**args)
        item = sheet(); item['positions'][0] = np.nan
        with self.assertRaises(ValueError):
            fit_front_bend([item])

    def test_preparation_adapter_round_trip_preserves_unsupported_geometry(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source.glb'
            fixture(source)
            run_optical_group_preparation(source, root / 'original', grouping_mode='explicit_declarations',
                                          declarations=declarations(source))
            report = run_smooth_optical_preparation(root / 'original/report.json', root / 'alternative')
            self.assertEqual(report['status'], 'prepared_optical_group_candidate')
            self.assertEqual(report['smooth_optical_geometry']['changed_groups'], 0)
            self.assertEqual((root / 'alternative/source.glb').read_bytes(), source.read_bytes())
            self.assertEqual(report['groups'][0]['primitives'], json.loads((root / 'original/report.json').read_bytes())['groups'][0]['primitives'])
            with self.assertRaises(ValueError):
                run_smooth_optical_preparation(root / 'original/report.json', root / 'alternative')

    def test_supported_preparation_exports_verified_new_normals_and_exact_triangles(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'source.glb'; item = sheet()
            arrays = [np.asarray(item['positions'], '<f4'), np.asarray(item['indices'], '<u4').ravel(),
                      np.tile(np.array([0., 0., 1.], '<f4'), (len(item['positions']), 1))]
            binary, views, accessors = bytearray(), [], []
            for value, kind, width in zip(arrays, [5126, 5125, 5126], ['VEC3', 'SCALAR', 'VEC3']):
                views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': value.nbytes})
                binary.extend(value.tobytes())
                accessors.append({'bufferView': len(views) - 1, 'componentType': kind, 'type': width, 'count': len(value)})
            doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}], 'bufferViews': views,
                   'accessors': accessors, 'materials': [{'name': 'lens'}], 'meshes': [{'primitives': [
                   {'attributes': {'POSITION': 0, 'NORMAL': 2}, 'indices': 1, 'material': 0}]}],
                   'nodes': [{'mesh': 0}], 'scenes': [{'nodes': [0]}], 'scene': 0}
            source.write_bytes(_pack_glb(doc, binary))
            original = run_optical_group_preparation(source, root / 'original', grouping_mode='explicit_declarations',
                declarations=declarations(source, [('lens', [0])]))
            report = run_smooth_optical_preparation(root / 'original/report.json', root / 'alternative')
            self.assertEqual(report['smooth_optical_geometry']['changed_groups'], 1)
            self.assertNotEqual(report['model']['sha256'], original['model']['sha256'])
            first, second = load_glb(root / 'original/prepared-neutral.glb'), load_glb(root / 'alternative/prepared-neutral.glb')
            np.testing.assert_array_equal(first.vertices, second.vertices)
            np.testing.assert_array_equal(first.faces, second.faces)
            self.assertEqual(report['source_sha256'], original['source_sha256'])


if __name__ == '__main__':
    unittest.main()
