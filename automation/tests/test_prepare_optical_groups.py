"""Real source-bound grouped preparation, not provider or product-specific tests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from reconstruction.deform_glb import _read
from reconstruction.lens_asset import _pack_glb
from reconstruction.mesh import load_glb
from reconstruction.optical_group_asset import read_optical_group_candidate
from reconstruction.prepare_optical_groups import run_optical_group_preparation, validate_group_declarations
from test_deform_glb import fixture


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def declarations(source, memberships=None):
    mesh = load_glb(source)
    return {'schema_version': 1, 'source_sha256': sha(source),
        'coordinate_frame': {'id': 'source-world', 'units': 'unitless', 'up_axis': '+Y', 'forward_axis': '+Z',
                             'provenance': {'method': 'fixture selected-scene world, product axes unverified'}},
        'provenance': {'method': 'explicit test declaration; identity unverified'},
        'groups': [{'group_id': gid, 'members': [{'id': f'member-{index}', 'source_part_index': index,
            'source_binding': {key: mesh.parts[index][key] for key in ('node_index', 'mesh_index', 'primitive_index')}}
            for index in indices]} for gid, indices in (memberships or [('whole-optical-object', [0, 1])])]}


class PrepareOpticalGroupsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name); self.source = self.folder/'source.glb'; self.output = self.folder/'prepared'
        fixture(self.source)

    def run_stage(self, **kwargs):
        declared = kwargs.pop('declarations') if 'declarations' in kwargs else declarations(self.source)
        return run_optical_group_preparation(self.source, self.output,
            grouping_mode=kwargs.pop('grouping_mode', 'explicit_declarations'),
            declarations=declared, **kwargs)

    def reload(self, report):
        receipt = json.loads((self.output/report['export']['path']).read_text())
        return read_optical_group_candidate(self.output/report['model']['path'], receipt)

    def saved_group(self, row):
        prepared = {'report': json.loads((self.output/row['prepared_report']['path']).read_text()), 'primitives': []}
        self.assertEqual(sha(self.output/row['prepared_report']['path']), row['prepared_report']['sha256'])
        for item in row['primitives']:
            self.assertEqual(sha(self.output/item['path']), item['sha256'])
            with np.load(self.output/item['path'], allow_pickle=False) as arrays:
                prepared['primitives'].append({'id': item['id'], **{key: arrays[key] for key in arrays.files}})
        return prepared

    def test_multipart_transforms_common_height_and_exact_saved_source(self):
        before = self.source.read_bytes(); original = load_glb(self.source)
        report = self.run_stage()
        self.assertEqual(report['status'], 'prepared_optical_group_candidate')
        self.assertFalse(report['accepted']); self.assertEqual(report['quality_verdict'], 'unmeasured')
        self.assertIsNone(report['selected_material'])
        self.assertEqual(report['source'], str(self.source.resolve()))
        self.assertEqual((self.output/report['source_snapshot']['path']).read_bytes(), before)
        self.assertEqual(self.source.read_bytes(), before)
        saved = self.saved_group(report['groups'][0]); self.assertEqual(len(saved['primitives']), 2)
        lo, hi = original.vertices[:, 1].min(), original.vertices[:, 1].max()
        for index, item in enumerate(saved['primitives']):
            part = original.parts[index]; start, count = part['vertex_start'], part['vertex_count']
            np.testing.assert_array_equal(item['positions'], original.vertices[start:start+count])
            np.testing.assert_array_equal(item['indices'], original.faces[part['face_start']:part['face_start']+part['face_count']]-start)
            np.testing.assert_allclose(item['uv'][:, 1], (item['positions'][:, 1]-lo)/(hi-lo), atol=0, rtol=0)
            self.assertEqual(item['positions'].dtype, np.float64)
            self.assertEqual(item['indices'].dtype, np.int64)
            normal_receipt = saved['report']['primitives'][index]['normal_transform']
            self.assertEqual(normal_receipt['provenance']['source_sha256'], sha(self.source))
            np.testing.assert_allclose(item['normals'], [[0, 0, 1]]*3)
        actual = self.reload(report)
        self.assertEqual(len(actual['groups']), 1)
        self.assertEqual(actual['groups'][0]['source_part_indices'], [0, 1])

    def test_nonuniform_normal_transport(self):
        raw, doc, binary = _read(self.source); binary = bytearray(binary)
        acc = doc['accessors'][doc['meshes'][0]['primitives'][0]['attributes']['NORMAL']]
        view = doc['bufferViews'][acc['bufferView']]
        binary[view['byteOffset']:view['byteOffset']+36] = np.array([[1, 1, 1]]*3, dtype='<f4').tobytes()
        self.source.write_bytes(_pack_glb(doc, binary))
        report = self.run_stage(); saved = self.saved_group(report['groups'][0])
        expected = np.array([.5, 1/3, .25]); expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(saved['primitives'][0]['normals'], np.tile(expected, (3, 1)))
        receipt = saved['report']['primitives'][0]['normal_transform']
        np.testing.assert_allclose(receipt['inverse_transpose_matrix'], np.diag([.5, 1/3, .25]))

    def test_unused_vertices_and_invalid_unused_normals_do_not_set_height(self):
        _, doc, binary = _read(self.source); binary = bytearray(binary)
        def append(values):
            values = np.asarray(values, dtype='<f4'); binary.extend(b'\0'*(-len(binary) % 4))
            doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': values.nbytes})
            binary.extend(values.tobytes()); doc['accessors'].append({'bufferView': len(doc['bufferViews'])-1,
                'componentType': 5126, 'type': 'VEC3', 'count': len(values)})
            return len(doc['accessors'])-1
        attrs = doc['meshes'][0]['primitives'][0]['attributes']
        attrs['POSITION'] = append([[0, 0, 0], [1, 0, 0], [0, 1, 0], [50, 100, 20]])
        attrs['NORMAL'] = append([[0, 0, 1]]*3+[[np.nan, 0, 0]])
        doc['buffers'][0]['byteLength'] = len(binary)
        self.source.write_bytes(_pack_glb(doc, binary))
        report = self.run_stage(); self.assertEqual(report['status'], 'prepared_optical_group_candidate')
        saved = self.saved_group(report['groups'][0])
        for item, row in zip(saved['primitives'], saved['report']['primitives']):
            self.assertEqual(len(item['positions']), 4); self.assertEqual(row['unused_vertex_count'], 1)
            self.assertEqual(row['invalid_unused_authored_normals_replaced'], 1)
            self.assertIn('preserved', row['normal_policy'])
            np.testing.assert_array_equal(item['uv'][3], [.5, .5])
            np.testing.assert_array_equal(item['normals'][3], [0, 0, 1])
        self.assertLess(saved['report']['referenced_bounds']['maximum'][1], 4)

    def test_missing_normals_are_an_explicit_derived_hypothesis(self):
        fixture(self.source, mutate=lambda doc: doc['meshes'][0]['primitives'][0]['attributes'].pop('NORMAL'))
        report = self.run_stage(); saved = self.saved_group(report['groups'][0])
        for item in saved['report']['primitives']:
            self.assertIn('derived area-weighted normal hypothesis', item['normal_policy'])
            self.assertEqual(item['source_normal_issue'], 'missing')
        self.reload(report)

    def test_auto_complete_ledger_and_shared_material_independent_groups(self):
        report = self.run_stage(grouping_mode='source_part_hypotheses', declarations=None)
        self.assertEqual(report['status'], 'prepared_optical_group_candidate')
        self.assertEqual([g['group_id'] for g in report['groups']], ['optical-n1-m0-p0', 'optical-n2-m0-p0'])
        self.assertEqual(len(report['source_parts']), 2)
        self.assertTrue(all(row['selected_for_group_preparation'] for row in report['source_parts']))
        self.assertEqual(len(self.reload(report)['groups']), 2)
        self.assertIsNone(report['declarations'])

    def test_auto_no_selection_and_unselected_ledger_are_retained(self):
        def mutate(doc):
            doc['nodes'][1]['extras'].pop('partRole'); doc['materials'][0]['name'] = 'frame'
        fixture(self.source, mutate=mutate)
        report = self.run_stage(grouping_mode='source_part_hypotheses', declarations=None)
        self.assertEqual(report['status'], 'no_candidate_optical_parts')
        self.assertEqual(len(report['source_parts']), 2)
        self.assertTrue(all(not row['selected_for_group_preparation'] for row in report['source_parts']))
        self.assertIsNone(report['model']); self.assertIsNone(report['export']); self.assertEqual(report['groups'], [])
        self.assertFalse((self.output/'prepared-neutral.glb').exists())

    def test_sha_and_binding_mismatches_record_unavailability_not_partial_export(self):
        for label in ('sha', 'part', 'binding'):
            with self.subTest(label=label):
                self.output = self.folder/label; declared = declarations(self.source)
                if label == 'sha':
                    declared['source_sha256'] = '0'*64
                elif label == 'part':
                    declared['groups'][0]['members'][0]['source_part_index'] = 99
                else:
                    declared['groups'][0]['members'][0]['source_binding']['mesh_index'] = 99
                report = self.run_stage(declarations=declared)
                self.assertEqual(report['status'], 'unsupported_optical_group_preparation')
                self.assertTrue(report['reasons']); self.assertEqual(report['groups'], [])
                self.assertIsNone(report['model']); self.assertIsNone(report['export'])
                self.assertFalse((self.output/'prepared-neutral.glb').exists())

    def test_one_unsupported_group_prevents_complete_or_partial_export(self):
        # Source triangles are intact, but the second instance has zero Y span.
        def mutate(doc):
            doc['nodes'][2]['rotation'] = [2**-.5, 0, 0, 2**-.5]
            doc['nodes'][2]['matrix'] = [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 3, 0, 0, 1]
        fixture(self.source, mutate=mutate)
        report = self.run_stage(declarations=declarations(self.source, [('supported', [0]), ('unsupported', [1])]))
        self.assertEqual(report['status'], 'unsupported_optical_group_preparation')
        self.assertEqual(len(report['groups']), 2)
        self.assertEqual(len(report['groups'][0]['primitives']), 1)
        self.assertEqual(report['groups'][1]['primitives'], [])
        self.assertIsNone(report['model']); self.assertFalse((self.output/'prepared-neutral.glb').exists())

    def test_declaration_bytes_retained_and_source_captured_for_export(self):
        value = declarations(self.source); path = self.folder/'declarations.json'
        raw = json.dumps(value, indent=4).encode(); path.write_bytes(raw)
        original_read = Path.read_bytes; source_reads = []
        def tracking(instance):
            if instance == self.source:
                source_reads.append(instance)
            return original_read(instance)
        with patch.object(Path, 'read_bytes', tracking):
            report = self.run_stage(declarations=path)
        self.assertEqual(len(source_reads), 2, 'One computational capture and one final integrity recheck')
        self.assertEqual((self.output/report['declarations']['path']).read_bytes(), raw)
        self.assertEqual(report['declarations']['sha256'], hashlib.sha256(raw).hexdigest())
        receipt = json.loads((self.output/report['export']['path']).read_text())
        self.assertEqual(receipt['source_path'], str(self.output/'source.glb'))

    def test_export_rejection_is_recorded_without_partial_candidate(self):
        with patch('reconstruction.prepare_optical_groups.write_optical_group_candidate',
                   side_effect=ValueError('float32 coincident contract failed')):
            report = self.run_stage()
        self.assertEqual(report['status'], 'unsupported_optical_group_preparation')
        self.assertIn('float32 coincident', report['reasons'][0])
        self.assertIsNone(report['model']); self.assertIsNone(report['export'])
        self.assertFalse((self.output/'prepared-neutral.glb').exists())
        self.assertEqual(len(self.saved_group(report['groups'][0])['primitives']), 2)

    def test_source_changes_during_snapshot_export_invalidate_completed_receipt(self):
        from reconstruction.optical_group_asset import write_optical_group_candidate
        original = self.source.read_bytes()
        def changed_source(*args, **kwargs):
            # The exporter receives the captured snapshot, not this changed path.
            self.source.write_bytes(b'changed original')
            return write_optical_group_candidate(*args, **kwargs)
        with patch('reconstruction.prepare_optical_groups.write_optical_group_candidate', side_effect=changed_source):
            with self.assertRaisesRegex(ValueError, 'Source or implementation changed'):
                self.run_stage()
        self.assertEqual((self.output/'source.glb').read_bytes(), original)
        self.assertFalse((self.output/'report.json').exists())

    def test_strict_schema_duplicates_and_invalid_options_fail_before_mutation(self):
        base = declarations(self.source)
        invalids = []
        for field, value in [('schema_version', True), ('source_sha256', 'ABC'), ('groups', [])]:
            bad = deepcopy(base); bad[field] = value; invalids.append(bad)
        bad = deepcopy(base); bad['verified'] = True; invalids.append(bad)
        bad = deepcopy(base); bad['groups'][0]['members'][1] = deepcopy(bad['groups'][0]['members'][0]); invalids.append(bad)
        bad = deepcopy(base); bad['groups'][0]['group_id'] = '../escape'; invalids.append(bad)
        for bad in invalids:
            with self.assertRaises(ValueError):
                self.run_stage(declarations=bad)
            self.assertFalse(self.output.exists())
        path = self.folder/'duplicate.json'; path.write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON'):
            self.run_stage(declarations=path)
        for mode, value in [('automatic', None), ('explicit_declarations', None), ('source_part_hypotheses', base)]:
            with self.assertRaises(ValueError):
                self.run_stage(grouping_mode=mode, declarations=value)
        self.assertFalse(self.output.exists())
        self.assertEqual(validate_group_declarations(base), base)

    def test_nonempty_output_and_reparse_path_refused_without_writes(self):
        self.output.mkdir(); marker = self.output/'keep'; marker.write_bytes(b'unchanged')
        with self.assertRaisesRegex(ValueError, 'empty'):
            self.run_stage()
        self.assertEqual(marker.read_bytes(), b'unchanged')
        link = self.folder/'linked.glb'
        try:
            link.symlink_to(self.source)
        except OSError:
            return  # Windows account may lack symbolic-link privileges.
        with self.assertRaisesRegex(ValueError, 'reparse'):
            run_optical_group_preparation(link, self.folder/'new', grouping_mode='source_part_hypotheses')
        self.assertFalse((self.folder/'new').exists())


if __name__ == '__main__':
    unittest.main()
