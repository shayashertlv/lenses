"""Common rounding witnesses, exact normal hulls, budgets and cross-language cases."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import numpy as np
from scipy.optimize import linprog

from reconstruction.optical_group_runtime import EffectiveOpticalRuntimePolicy, validate_effective_optical_runtime
from optical_group_runtime_fixtures import fixed_cases, group, primitive, serializable


class OpticalRuntimeTests(unittest.TestCase):
    def test_fixed_counterexamples_and_immutability(self):
        for case in fixed_cases():
            with self.subTest(case=case['id']):
                before = json.dumps(serializable([case]), sort_keys=True)
                if case['id'] == 'zero_corner':
                    with self.assertRaisesRegex(ValueError, 'nonzero'):
                        validate_effective_optical_runtime(case['groups'])
                    continue
                report = validate_effective_optical_runtime(case['groups'])
                self.assertEqual(report['complete'], case['expected'])
                self.assertFalse(report['accepted']); self.assertEqual(report['quality_verdict'], 'unmeasured')
                if case['reason']:
                    self.assertIn(case['reason'], str(report['reasons']))
                if case['id'] == 'off_midpoint_common_height':
                    self.assertEqual(report['height'][0]['proof'], 'exact_common_endpoint_halfplane_intersection')
                json.dumps(report, allow_nan=False)
                self.assertEqual(json.dumps(serializable([case]), sort_keys=True), before)

    def test_random_integer_normal_hulls_against_independent_feasibility_and_browser(self):
        rng = np.random.default_rng(981201)
        cases = fixed_cases()
        for index in range(96):
            normals = rng.integers(-6, 7, size=(3, 3))
            if index % 3 == 0: normals[:, 2] = 0
            elif index % 3 == 1: normals[:, 2] = 2*normals[:, 0]-normals[:, 1]
            if np.any(np.all(normals == 0, axis=1)):
                normals[np.all(normals == 0, axis=1)] = [1, 0, 0]
            # A separate floating LP feasibility test on small integers, not the
            # exact cross-product implementation. Borderline noninteger data
            # is reserved for the explicit exact-rank counterexample above.
            feasible = linprog(np.zeros(3), A_eq=np.vstack([normals.T, np.ones(3)]),
                               b_eq=[0, 0, 0, 1], bounds=(0, None), method='highs').success
            case = {'id': f'integer-normals-{index}', 'expected': not feasible,
                    'groups': [group(members=[primitive(normals=normals)])], 'reason': None}
            self.assertEqual(validate_effective_optical_runtime(case['groups'])['complete'], not feasible)
            cases.append(case)
        node = shutil.which('node'); ar = Path(__file__).resolve().parents[2]/'ar'
        if node is None or not (ar/'node_modules/three').exists():
            self.skipTest('Cross-language parity requires the existing local AR Node dependencies')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'cases.json'; path.write_text(json.dumps(serializable(cases)), encoding='utf-8')
            process = subprocess.run([node, str(Path(__file__).with_name('optical_group_runtime_parity.mjs')), str(path)],
                                     capture_output=True, text=True, timeout=45, check=True)
            results = json.loads(process.stdout)
        self.assertEqual(len(results), len(cases))
        for case, actual in zip(cases, results):
            self.assertEqual(actual['id'], case['id'])
            self.assertEqual(actual['compatible'], case['expected'], actual)

    def test_resource_refusals_never_partial_success(self):
        inputs = [group(members=[primitive(), primitive('duplicate')])]
        for member in inputs[0]['primitives']: member['positions'][1, 0] = 2
        for name, value, reason in (
                ('maximum_primitives', 1, 'mesh_budget_exceeded'),
                ('maximum_vertices', 5, 'vertex_budget_exceeded'),
                ('maximum_triangles', 1, 'triangle_budget_exceeded'),
                ('maximum_coordinate_integer_bits', 1, 'exact_coordinate_integer_budget_exceeded'),
                ('maximum_arithmetic_integer_bits', 8, 'arithmetic_integer_budget_exceeded'),
                ('maximum_arithmetic_work', 1, 'arithmetic_work_budget_exceeded'),
                ('maximum_broadphase_work', 1, 'broadphase_work_budget_exceeded')):
            with self.subTest(name=name):
                report = validate_effective_optical_runtime(inputs, policy=EffectiveOpticalRuntimePolicy(**{name:value}))
                self.assertFalse(report['complete']); self.assertFalse(report['accepted'])
                self.assertIn(reason, str(report['reasons']))
        off = next(c for c in fixed_cases() if c['id'] == 'off_midpoint_common_height')
        report = validate_effective_optical_runtime(off['groups'], policy=EffectiveOpticalRuntimePolicy(maximum_height_polygon_vertices=1))
        self.assertIn('height_polygon_budget_exceeded', report['reasons'])
        report = validate_effective_optical_runtime([group(str(i)) for i in range(9)])
        self.assertIn('group_budget_exceeded', report['reasons'])
        with self.assertRaises(ValueError): EffectiveOpticalRuntimePolicy(maximum_groups=9)
        with self.assertRaises(ValueError): EffectiveOpticalRuntimePolicy(maximum_groups=True)

    def test_runtime_requires_actual_float32_and_valid_unused_attributes(self):
        for mode in ('float64', 'unused-nan', 'invalid-index'):
            g = group(); member = g['primitives'][0]
            if mode == 'float64': member['positions'] = member['positions'].astype(float)
            elif mode == 'invalid-index': member['indices'][0, 0] = 30
            else:
                for key, value in (('positions', [4, 4, 4]), ('normals', [np.nan, 0, 1]), ('uv', [.5, .5])):
                    member[key] = np.vstack([member[key], np.asarray(value, dtype='f4')])
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                validate_effective_optical_runtime([g])


if __name__ == '__main__': unittest.main()
