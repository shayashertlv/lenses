"""Failed-export recovery must preserve geometry and reproduce its narrow failure."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pytest

from reconstruction.deform_glb import _read
from reconstruction.lens_asset import _pack_glb
from reconstruction.optical_group_asset import read_optical_group_candidate
from reconstruction.prepare_optical_groups import run_optical_group_preparation
from reconstruction.segmented_optics import recover_smooth_failed_preparation
from test_prepare_optical_groups import declarations


def conflicting_fixture(path, *, conflict=True, grid=14):
    x, y = np.meshgrid(np.linspace(-.5, .5, grid), np.linspace(-.25, .25, grid))
    p = np.column_stack((x.ravel(), y.ravel(), -.32*x.ravel()**2))
    a = np.arange(grid*grid).reshape(grid, grid)[:-1, :-1].ravel()
    f = np.concatenate((np.column_stack((a, a+1, a+grid)), np.column_stack((a+1, a+grid+1, a+grid))))
    count = len(p)
    positions = np.concatenate((p, p)).astype('<f4')
    indices = np.concatenate((f, f+count)).astype('<u4')
    first = np.column_stack((.64*p[:, 0], np.zeros(count), np.ones(count)))
    second = first.copy()
    if conflict:
        second[:, 0] += .4
    normals = np.concatenate((first, second))
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    uv = np.tile(np.column_stack((x.ravel()+.5, y.ravel()*2+.5)), (2, 1)).astype('<f4')
    binary, views, accessors = bytearray(), [], []
    def add(array, kind, component=5126):
        array = np.asarray(array, '<f4' if component == 5126 else '<u4')
        binary.extend(b'\0'*(-len(binary) % 4))
        views.append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': array.nbytes})
        binary.extend(array.tobytes())
        accessors.append({'bufferView': len(views)-1, 'componentType': component, 'type': kind, 'count': len(array)})
        return len(accessors)-1
    lens = {'attributes': {'POSITION': add(positions, 'VEC3'), 'NORMAL': add(normals, 'VEC3'),
                           'TEXCOORD_0': add(uv, 'VEC2')}, 'indices': add(indices.ravel(), 'SCALAR', 5125), 'material': 0}
    frame = {'attributes': {'POSITION': add([[.6, 0., .1], [.7, 0., .1], [.6, .1, .1]], 'VEC3'),
                            'NORMAL': add([[0, 0, 1]]*3, 'VEC3'), 'TEXCOORD_0': add([[0, 0], [1, 0], [0, 1]], 'VEC2')},
             'indices': add([0, 1, 2], 'SCALAR', 5125), 'material': 1}
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}], 'bufferViews': views,
           'accessors': accessors, 'materials': [{'name': 'unlabeled lens'}, {'name': 'retained gold',
             'pbrMetallicRoughness': {'baseColorFactor': [.8, .6, .2, 1], 'roughnessFactor': .3}}],
           'meshes': [{'primitives': [lens]}, {'primitives': [frame]}], 'nodes': [{'mesh': 0}, {'mesh': 1}],
           'scenes': [{'nodes': [0, 1]}], 'scene': 0}
    path.write_bytes(_pack_glb(doc, binary))


class SegmentedOpticsRecoveryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def prepare(self, **kwargs):
        source = self.root/'source.glb'; conflicting_fixture(source, **kwargs)
        output = self.root/'failed'
        report = run_optical_group_preparation(source, output, grouping_mode='explicit_declarations',
                                               declarations=declarations(source, [('lens', [0])]))
        return output, report

    @pytest.mark.slow   # ~18 s
    def test_real_conflicting_normals_recover_without_geometry_frame_or_uv_changes(self):
        failed, original = self.prepare()
        self.assertEqual(original['status'], 'unsupported_optical_group_preparation')
        self.assertIn('conflicting_corresponding_corner_directions', original['reasons'][0])
        pins = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in failed.rglob('*') if p.is_file()}
        output = self.root/'recovered'
        report = recover_smooth_failed_preparation(failed/'report.json', output)
        self.assertEqual(report['status'], 'prepared_optical_group_candidate')
        self.assertFalse(report['accepted'])
        self.assertEqual(report['normal_recovery']['source_faces_deleted'], 0)
        before = np.load(failed/original['groups'][0]['primitives'][0]['path'])
        after = np.load(output/report['groups'][0]['primitives'][0]['path'])
        with before, after:
            for field in ('positions', 'indices', 'uv'):
                np.testing.assert_array_equal(before[field], after[field])
            self.assertFalse(np.array_equal(before['normals'], after['normals']))
        export = json.loads((output/report['export']['path']).read_text())
        read_optical_group_candidate(output/report['model']['path'], export, expected_sha256=report['model']['sha256'])
        self.assertFalse(export['demoted_legacy_optical_parts']); self.assertFalse(export['interior_contact_parts'])
        _, old_doc, old_binary = _read(failed/'source.glb')
        _, new_doc, new_binary = _read(output/'prepared-neutral.glb')
        self.assertEqual(old_binary, new_binary[:len(old_binary)])
        self.assertEqual(old_doc['materials'], new_doc['materials'][:len(old_doc['materials'])])
        self.assertEqual(old_doc['meshes'][1], new_doc['meshes'][1])
        self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in pins.items()))
        with self.assertRaisesRegex(ValueError, 'new or empty'):
            recover_smooth_failed_preparation(failed/'report.json', output)

    def test_unrelated_failure_is_not_recovered(self):
        failed, original = self.prepare()
        for reasons in [['lens:unsupported_geometry'], [original['reasons'][0].replace('conflicting_corresponding_corner_directions', 'different_optical_groups_overlap')]]:
            with self.subTest(reasons=reasons):
                changed = deepcopy(original); changed['reasons'] = reasons
                (failed/'report.json').write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    recover_smooth_failed_preparation(failed/'report.json', self.root/'recovered')

    def test_changed_prepared_archive_rejected_before_fit(self):
        failed, report = self.prepare()
        artifact = failed/report['groups'][0]['primitives'][0]['path']
        artifact.write_bytes(artifact.read_bytes()+b'changed')
        with self.assertRaisesRegex(ValueError, 'source bytes changed'):
            recover_smooth_failed_preparation(failed/'report.json', self.root/'recovered')

    def test_missing_archive_attribute_is_not_guessed(self):
        failed, report = self.prepare()
        reference = report['groups'][0]['primitives'][0]
        artifact = failed/reference['path']
        with np.load(artifact) as arrays:
            incomplete = {key: arrays[key].copy() for key in arrays.files if key != 'normals'}
        np.savez_compressed(artifact, **incomplete)
        reference['sha256'] = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (failed/'report.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'Incomplete prepared geometry'):
            recover_smooth_failed_preparation(failed/'report.json', self.root/'recovered')

    def test_recorded_failure_must_be_reproduced_by_actual_pinned_arrays(self):
        failed, report = self.prepare(conflict=False)
        self.assertEqual(report['status'], 'prepared_optical_group_candidate')
        report.update(status='unsupported_optical_group_preparation', model=None, export=None,
            reasons=['export_unsupported: Actual float32 optical runtime/coincident-patch contract unsupported: ["conflicting_corresponding_corner_directions"]'])
        (failed/'report.json').write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'does not reproduce'):
            recover_smooth_failed_preparation(failed/'report.json', self.root/'recovered')

    def test_existing_minimum_support_guard_is_not_relaxed(self):
        failed, report = self.prepare(grid=3)
        self.assertEqual(report['status'], 'unsupported_optical_group_preparation')
        with self.assertRaisesRegex(ValueError, 'smooth-fit guards do not support'):
            recover_smooth_failed_preparation(failed/'report.json', self.root/'recovered')


if __name__ == '__main__':
    unittest.main()
